"""A tiny calculator with a built-in self-test."""


def add(a, b):
    return a + b


def divide(a, b):
    return a / b


if __name__ == "__main__":
    assert add(2, 3) == 5
    assert divide(10, 2) == 5
    print("self-tests passed")
