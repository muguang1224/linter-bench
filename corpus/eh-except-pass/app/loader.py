def load(path):
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        pass
