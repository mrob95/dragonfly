from .engine import DraconityEngine

def get_engine(**kwargs):
    """ Retrieve the Draconity back-end engine object. """
    _engine = DraconityEngine(**kwargs)
    return _engine
