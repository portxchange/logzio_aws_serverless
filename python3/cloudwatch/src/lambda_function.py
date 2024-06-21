import base64
import gzip
import json
import logging
import os
import re
from io import BytesIO

from python3.shipper.shipper import LogzioShipper

KEY_INDEX = 0
VALUE_INDEX = 1
LOG_LEVELS = ['alert', 'trace', 'debug', 'notice', 'info', 'warn',
              'warning', 'error', 'err', 'critical', 'crit', 'fatal',
              'severe', 'emerg', 'emergency']

PYTHON_EVENT_SIZE = 3
NODEJS_EVENT_SIZE = 4
LAMBDA_LOG_GROUP = '/aws/lambda/'
pattern = r"\[(.*)\] - (.*) - (.*)"

# set logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _extract_aws_logs_data(event):
    # type: (dict) -> dict
    event_str = event['awslogs']['data']
    try:
        logs_data_decoded = base64.b64decode(event_str)
        logs_data_unzipped = gzip.GzipFile(fileobj=BytesIO(logs_data_decoded))
        logs_data_unzipped = logs_data_unzipped.read()
        logs_data_dict = json.loads(logs_data_unzipped)
        return logs_data_dict
    except ValueError as e:
        logger.error("Got exception while loading json, message: {}".format(e))
        raise ValueError("Exception: json loads")


def _extract_lambda_log_message(log):
    # type: (dict) -> None
    str_message = str(log['message'])
    try:
        start_level = str_message.index('[')
        end_level = str_message.index(']')
        log_level = str_message[start_level + 1:end_level]
        if log_level.lower() in LOG_LEVELS:
            log['log_level'] = log_level
        start_split = end_level + 2
    except ValueError:
        # Let's try without log level
        start_split = 0
    message_parts = str_message[start_split:].split('\t')
    size = len(message_parts)
    if size == PYTHON_EVENT_SIZE or size == NODEJS_EVENT_SIZE:
        log['@timestamp'] = message_parts[0]
        log['requestID'] = message_parts[1]
        log['message'] = message_parts[size - 1]
    if size == NODEJS_EVENT_SIZE:
        log['log_level'] = message_parts[2]


def _add_timestamp(log):
    # type: (dict) -> None
    if '@timestamp' not in log:
        log['@timestamp'] = str(log['timestamp'])
        del log['timestamp']


def remove_ansi_escape_codes(text):
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)


def safe_get(dictionary, key):
    return dictionary.get(key, '')


def _handle_nginx_controller_logs(log, original_app):
    request_url = f"{log['vhost']}{log['request']}"
    duration = safe_get(log, 'duration')
    status = safe_get(log, 'status')
    bytes_sent = safe_get(log, 'bytes_sent')
    log['original_app'] = original_app
    log['message'] = f"{request_url} status = {status} duration = {duration} bytes sent = {bytes_sent}"


def _handle_json_log_message(log, log_json):
    log['message'] = remove_ansi_escape_codes(safe_get(log_json, 'message'))


def _parse_pronto_logs_with_regular_expression(log, log_line):
    match = re.match(pattern, log_line)
    log['log_level'] = match.group(1).upper()
    log['logger'] = match.group(2)
    log['message'] = match.group(3)


def _parse_to_json(log):
    # type: (dict) -> None
    try:
        if os.environ['FORMAT'].lower() == 'json':
            json_object = json.loads(log['message'])
            for key, value in json_object.items():
                log[key] = value
            log['original_message'] = log['message']
            original_app = safe_get(log, 'app')

            # in the case of nginx-ingress-controller, original requested app name captured as original_app
            log['app'] = log['kubernetes']['container_name']

            if 'vhost' in log and 'request' in log and 'status' in log:
                _handle_nginx_controller_logs(log, original_app)
            elif 'log' in log:
                log_string = log['log']
                try:
                    log_json = json.loads(log_string)
                    _handle_json_log_message(log, log_json)
                except (KeyError, ValueError) as e:
                    log_line = remove_ansi_escape_codes(log_string)
                    try:
                        _parse_pronto_logs_with_regular_expression(log, log_line)
                    except (KeyError, ValueError) as e:
                        log['message'] = log_line
                        pass
                del log['log']
    except (KeyError, ValueError) as e:
        pass


def _parse_cloudwatch_log(log, additional_data):
    # type: (dict, dict) -> bool
    _add_timestamp(log)
    if LAMBDA_LOG_GROUP in additional_data['logGroup']:
        if _is_valid_log(log):
            _extract_lambda_log_message(log)
        else:
            return False
    if not (_filter_out_by_log_stream_name(additional_data)):
        log.update(additional_data)
        _parse_to_json(log)
        return True
    else:
        return False


def _filter_out_by_log_stream_name(additional_data):
    try:
        if os.environ['STREAM_NAME']:
            stream_to_be_filter_out = os.environ['STREAM_NAME'].split(";")
            return additional_data['logStream'].startswith(tuple(stream_to_be_filter_out))
    except KeyError as e:
        return False


def _get_additional_logs_data(aws_logs_data, context):
    # type: (dict, 'LambdaContext') -> dict
    additional_fields = ['logGroup', 'logStream', 'messageType', 'owner']
    additional_data = dict((key, aws_logs_data[key]) for key in additional_fields)
    try:
        additional_data['function_version'] = context.function_version
        additional_data['invoked_function_arn'] = context.invoked_function_arn
    except KeyError:
        logger.info('Failed to find context value. Continue without adding it to the log')

    try:
        # If ENRICH has value, add the properties
        if os.environ['ENRICH']:
            properties_to_enrich = os.environ['ENRICH'].split(";")
            for property_to_enrich in properties_to_enrich:
                property_key_value = property_to_enrich.split("=")
                additional_data[property_key_value[KEY_INDEX]] = property_key_value[VALUE_INDEX]
    except KeyError:
        pass

    try:
        additional_data['type'] = os.environ['TYPE']
    except KeyError:
        logger.info("Using default TYPE 'logzio_cloudwatch_lambda'.")
        additional_data['type'] = 'logzio_cloudwatch_lambda'
    return additional_data


def _is_valid_log(log):
    # type (dict) -> bool
    message = log['message']
    is_info_log = message.startswith('START') or message.startswith('END') or message.startswith('REPORT')
    return not is_info_log


def lambda_handler(event, context):
    # type (dict, 'LambdaContext') -> None

    aws_logs_data = _extract_aws_logs_data(event)
    additional_data = _get_additional_logs_data(aws_logs_data, context)
    shipper = LogzioShipper()
    max_size_of_log = int(os.getenv('MAX_LOG_SIZE', 10000))

    logger.info("About to send {} logs".format(len(aws_logs_data['logEvents'])))
    for log in aws_logs_data['logEvents']:
        if not isinstance(log, dict):
            raise TypeError("Expected log inside logEvents to be a dict but found another type")
        if _parse_cloudwatch_log(log, additional_data):
            json_log = json.dumps(log)
            if len(json_log) <= max_size_of_log:
                shipper.add(log)
            else:
                logger.warning("Sending to logzio SKIPPED, size of json string to be pushed is " + str(len(json_log)))

    shipper.flush()
