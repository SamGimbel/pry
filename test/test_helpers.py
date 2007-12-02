import libpry
import libpry.helpers as he

def err(*args, **kwargs):
    raise ValueError(str(args) + str(kwargs))

def ok(): pass

class uRaises(libpry.AutoTree):
    def test_class(self):
        he.raises(ValueError, err, "Hello")
        he.raises(
            "expected assertionerror",
            he.raises, AssertionError, err, "Hello"
        )

    def test_string(self):
        he.raises("test error", err, "Test Error")
        he.raises(
            "expected",
            he.raises, "test error", err, "Hello"
        )

    def test_noexception(self):
        he.raises(
            "no exception",
            he.raises, "some exception", ok
        )


tests = [
    uRaises()
]
