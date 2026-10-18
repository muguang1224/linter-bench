def load(path):
    try:
        with open(path) as handle:
            return handle.read()
    except OSError as exc:
        raise RuntimeError("cannot read") from exc
