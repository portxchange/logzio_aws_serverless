#  AWS Serverless Shipper - Lambda

This is an AWS Lambda function that ships logs from AWS services to Logz.io. 

**Note**:
This project contains code for Python 2 and Python 3.
We urge you to use Python 3 because Python 2.7 will reach end of life on January 1, 2020.
 
[Get started with Python 3](https://github.com/logzio/logzio_aws_serverless/tree/master/python3)

We forked and modified few changes. Here is the list

- don't push logs bigger than max limit
- published log json structured changed after moving from fluentd to fluent bit so adjusted back however we want

## Deployment 

- Make zip file with the script `./create-zipfile.sh`
- 