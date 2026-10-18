from app.thing import Thing

def test_thing():
    thing = Thing()
    assert repr(thing) == "Thing(1)"
