class SafeError(ValueError):
    """Only a fixed reason code may cross a trust boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)
