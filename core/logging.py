import logging
import re


class PrivateAccessFilter(logging.Filter):
    def filter(self, record):
        text = record.getMessage()
        text = re.sub(r"/invite/[^/ ?]+", "/invite/[redacted]", text)
        text = re.sub(r"([?&]token=)[^ &\"]+", r"\1[redacted]", text)
        record.msg, record.args = text, ()
        return True
