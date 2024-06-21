#!/usr/bin/env bash
set -e

cd python3/cloudwatch
mkdir -p dist/python3/shipper
cp -r ../shipper/shipper.py dist/python3/shipper \
  && cp src/lambda_function.py dist \
  && cd dist/ \
  && zip logzio-cloudwatch lambda_function.py python3/shipper/*

aws s3 cp ./logzio-cloudwatch.zip s3://maistro-infrastructure/logzio-cloudwatch-maistro.zip
aws s3 cp ./logzio-cloudwatch.zip s3://pronto-infrastructure/logzio-cloudwatch.zip