class TransportFailure(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class DirectorRefusal(Exception):
    def __init__(self, code, message, http_status=409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
