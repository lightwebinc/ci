#!/usr/bin/env python3
"""Tests for gen-third-party-licenses.py that need neither Go nor npm."""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gen", os.path.join(HERE, "gen-third-party-licenses.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

APACHE = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n"
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person\n"


def write(d, name, text):
    path = os.path.join(d, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Files(unittest.TestCase):
    def test_license_spellings_and_directory(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "license.md", MIT)
            write(d, "LICENSES/b.txt", "b")
            write(d, "LICENSES/a.txt", "a")
            write(d, "main.go", "package x")
            names = [os.path.relpath(p, d) for p in gen.license_files(d)]
            self.assertEqual(names, ["LICENSES/a.txt", "LICENSES/b.txt", "license.md"])

    def test_notice_is_not_a_licence_and_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "LICENSE", APACHE)
            write(d, "NOTICE", "thing\nCopyright 2026 Someone\n")
            write(d, "sub/NOTICE", "nested, not this package's")
            self.assertEqual([os.path.basename(p) for p in gen.license_files(d)], ["LICENSE"])
            self.assertEqual([os.path.relpath(p, d) for p in gen.notice_files(d)], ["NOTICE"])


class Body(unittest.TestCase):
    def test_notice_follows_the_licence_and_names_its_module(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "LICENSE", APACHE)
            write(d, "NOTICE.txt", "thing\nCopyright 2026 Someone\n")
            label, text = gen.body("example.com/thing", d, gen.license_files(d))
            self.assertEqual(label, "Apache License 2.0")
            self.assertLess(text.index("Version 2.0, January 2004"), text.index("NOTICE shipped with example.com/thing"))
            self.assertTrue(text.endswith("Copyright 2026 Someone"))

    def test_no_notice_leaves_the_licence_alone(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "LICENSE", MIT)
            label, text = gen.body("example.com/thing", d, gen.license_files(d))
            self.assertEqual(label, "MIT License")
            self.assertEqual(text, MIT.rstrip())

    def test_two_licences_in_one_package_are_both_named(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "LICENSE", MIT + "\n" + "Licensed under the Apache License, Version 2.0\n")
            label, _ = gen.body("example.com/thing", d, gen.license_files(d))
            self.assertEqual(label, "MIT License, Apache License 2.0")


if __name__ == "__main__":
    unittest.main()
