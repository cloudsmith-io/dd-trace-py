"""
Trace queries to aws api done via botocore client
"""
# 3p
from ddtrace.vendor import wrapt
from ddtrace import config
import botocore.client

# project
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...pin import Pin
from ...ext import SpanTypes, http, aws
from ...utils.formats import deep_getattr
from ...utils.wrappers import unwrap


# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ('action', 'params', 'path', 'verb')
TRACED_ARGS = ['params', 'path', 'verb']


def patch():
    if getattr(botocore.client, '_datadog_patch', False):
        return
    setattr(botocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)
    Pin(service='aws', app='aws').onto(botocore.client.BaseClient)


def unpatch():
    if getattr(botocore.client, '_datadog_patch', False):
        setattr(botocore.client, '_datadog_patch', False)
        unwrap(botocore.client.BaseClient, '_make_api_call')


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, '_endpoint._endpoint_prefix')

    with pin.tracer.trace('{}.command'.format(endpoint_name),
                          service='{}.{}'.format(pin.service, endpoint_name),
                          span_type=SpanTypes.HTTP) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        operation = None
        if args:
            operation = args[0]
            span.resource = '%s.%s' % (endpoint_name, operation.lower())

        else:
            span.resource = endpoint_name

        aws.add_span_arg_tags(span, endpoint_name, args, ARGS_NAME, TRACED_ARGS)

        region_name = deep_getattr(instance, 'meta.region_name')

        meta = {
            'aws.agent': 'botocore',
            'aws.operation': operation,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        result = original_func(*args, **kwargs)

        response_meta = result['ResponseMetadata']
        span.set_tag(http.STATUS_CODE, response_meta['HTTPStatusCode'])
        span.set_tag('retry_attempts', response_meta['RetryAttempts'])

        request_id = response_meta.get('RequestId')
        if request_id:
            span.set_tag('aws.requestid', request_id)

        # set analytics sample rate
        span.set_tag(
            ANALYTICS_SAMPLE_RATE_KEY,
            config.botocore.get_analytics_sample_rate()
        )

        return result
