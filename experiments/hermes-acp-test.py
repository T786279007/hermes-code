def reverse_string(s):
    return s[::-1]


if __name__ == "__main__":
    tests = ["hello", "world", "abcdef", ""]
    for t in tests:
        print(f"{t!r} -> {reverse_string(t)!r}")
