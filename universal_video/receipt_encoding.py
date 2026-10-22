"""Bounded, versioned JSON hash profile shared with draft 0063.

This encoding is for new terminal-finish hashes only. Existing artifact and
terminal-evidence hashes retain their original encoding. No authority or IO.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

PROFILE = 'diana20-canonical-json/v1'
MAX_DEPTH = 32
MAX_NUMBER_BYTES = 128


class ReceiptEncodingError(ValueError):
    pass


def _string(value):
    if '\x00' in value:
        raise ReceiptEncodingError('RECEIPT_JSON_NUL')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        raise ReceiptEncodingError('RECEIPT_JSON_UNICODE') from None
    return json.dumps(value, ensure_ascii=False)


def _number(value):
    if type(value) is float and not math.isfinite(value):
        raise ReceiptEncodingError('RECEIPT_JSON_NONFINITE')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number.adjusted()) > MAX_NUMBER_BYTES:
            raise ReceiptEncodingError('RECEIPT_JSON_NUMBER_BOUND')
        text = format(number, 'f')
    except (ValueError, InvalidOperation):
        raise ReceiptEncodingError('RECEIPT_JSON_NUMBER') from None
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    if number == 0:
        text = '0'
    if len(text) > MAX_NUMBER_BYTES:
        raise ReceiptEncodingError('RECEIPT_JSON_NUMBER_BOUND')
    return text


def canonical_bytes(value, *, max_bytes=131072):
    """UTF-8, C/Unicode key order, no whitespace, finite plain decimals.

    No Unicode normalization; duplicate keys, NUL and lone surrogates refuse.
    Numeric equality uses decimal JSON values, without exponents or -0.
    """
    parts=[];size=0
    def append(text):
        nonlocal size
        raw=text.encode('utf-8')
        size+=len(raw)
        if size>max_bytes:raise ReceiptEncodingError('RECEIPT_JSON_SIZE')
        parts.append(raw)
    def string(item):
        if len(item)>max_bytes:raise ReceiptEncodingError('RECEIPT_JSON_SIZE')
        append(_string(item))
    def encode(item, depth):
        if depth > MAX_DEPTH:
            raise ReceiptEncodingError('RECEIPT_JSON_DEPTH')
        if item is None:append('null');return
        if type(item) is bool:append('true' if item else 'false');return
        if type(item) is str:string(item);return
        if type(item) in (int, float, Decimal):append(_number(item));return
        if type(item) is list:
            append('[')
            for i,value in enumerate(item):
                if i:append(',')
                encode(value,depth+1)
            append(']');return
        if type(item) is dict and all(type(k) is str for k in item):
            append('{')
            for i,key in enumerate(sorted(item)):
                if i:append(',')
                string(key);append(':');encode(item[key],depth+1)
            append('}');return
        raise ReceiptEncodingError('RECEIPT_JSON_TYPE')
    encode(value,0)
    raw = b''.join(parts)
    if not 1 <= len(raw) <= max_bytes:
        raise ReceiptEncodingError('RECEIPT_JSON_SIZE')
    return raw


def strict_loads(raw, *, max_bytes=131072):
    if type(raw) is not bytes or not 1 <= len(raw) <= max_bytes:
        raise ReceiptEncodingError('RECEIPT_JSON_SIZE')
    def pairs(values):
        result = {}
        for key,value in values:
            if key in result:raise ReceiptEncodingError('RECEIPT_JSON_DUPLICATE')
            result[key] = value
        return result
    def nonfinite(_):raise ReceiptEncodingError('RECEIPT_JSON_NONFINITE')
    try:
        result = json.loads(raw.decode('utf-8', errors='strict'),
            parse_float=Decimal, parse_int=int, parse_constant=nonfinite,
            object_pairs_hook=pairs)
        canonical_bytes(result,max_bytes=max_bytes)
        return result
    except (UnicodeError,ValueError,RecursionError) as exc:
        if isinstance(exc,ReceiptEncodingError):raise
        raise ReceiptEncodingError('RECEIPT_JSON_INVALID') from None


def sha256(raw):return hashlib.sha256(raw).hexdigest()


def json_sha256(value, *, max_bytes=131072):
    return sha256(canonical_bytes(value,max_bytes=max_bytes))
