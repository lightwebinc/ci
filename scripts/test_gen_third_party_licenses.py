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


class Names(unittest.TestCase):
    def test_licence_named_files(self):
        for name in ("LICENSE", "license.md", "COPYING", "LICENSE-MIT", "LICENSE-APACHE-2.0.txt",
                     "LICENSE.BSD", "Licence_MIT.rst"):
            self.assertTrue(gen.is_license_name(name), name)
        for name in ("LICENSE-THIRD-PARTY", "LICENSE-3rdparty.txt", "LICENSE-3rdparty.csv", "license.go", "licenses.json", "NOTICE", "LICENSE.tmpl",
                     "unlicense-me", "LICENSE-"):
            self.assertFalse(gen.is_license_name(name), name)

    def test_plain_and_suffixed_are_both_reproduced(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "LICENSE", APACHE)
            write(d, "LICENSE-MIT", MIT)
            label, text = gen.body("example.com/thing", d, gen.license_files(d))
            self.assertEqual(label, "Apache License 2.0, MIT License")
            self.assertIn("Permission is hereby granted", text)


def package(d, path, name, version, licence):
    write(d, os.path.join(path, "package.json"), '{"name": "%s", "version": "%s"}' % (name, version))
    write(d, os.path.join(path, "LICENSE"), licence)


class Npm(unittest.TestCase):
    def test_error_document_is_a_failure_not_an_empty_tree(self):
        with tempfile.TemporaryDirectory() as d:
            doc = write(d, "ls.json", '{"error": {"code": "ENOENT", "summary": "no package.json"}}')
            os.environ["NPM_LS_JSON"] = doc
            try:
                with self.assertRaises(SystemExit) as e:
                    gen.npm_tree(d)
                self.assertIn("no package.json", str(e.exception))
            finally:
                del os.environ["NPM_LS_JSON"]

    def test_tree_with_problems_is_still_a_tree(self):
        with tempfile.TemporaryDirectory() as d:
            doc = write(d, "ls.json", '{"name": "x", "version": "1.0.0", "error": {"code": "ELSPROBLEMS"}, '
                                      '"dependencies": {"a": {"version": "1.0.0"}}}')
            os.environ["NPM_LS_JSON"] = doc
            try:
                self.assertIn("a", gen.npm_tree(d)["dependencies"])
            finally:
                del os.environ["NPM_LS_JSON"]

    def test_abbreviated_node_does_not_hide_the_expanded_one(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("a", "shared", "leaf"):
                package(d, "node_modules/" + n, n, "1.0.0", MIT)
            tree = {"name": "x", "dependencies": {
                "a": {"version": "1.0.0", "dependencies": {"shared": {"version": "1.0.0"}}},
                "shared": {"version": "1.0.0", "dependencies": {"leaf": {"version": "1.0.0"}}},
            }}
            names = [n for n, _, _ in gen.npm_packages(d, tree)]
            self.assertEqual(names, ["a", "leaf", "shared"])

    def test_each_version_is_read_from_where_it_is_installed(self):
        with tempfile.TemporaryDirectory() as d:
            package(d, "node_modules/parent", "parent", "1.0.0", MIT)
            package(d, "node_modules/shared", "shared", "2.0.0", APACHE)
            package(d, "node_modules/parent/node_modules/shared", "shared", "1.0.0", MIT)
            package(d, "node_modules/@s/scoped", "@s/scoped", "1.0.0", MIT)
            package(d, "node_modules/@s/scoped/node_modules/shared", "shared", "1.0.0", MIT)
            tree = {"name": "x", "dependencies": {
                "parent": {"version": "1.0.0", "dependencies": {"shared": {"version": "1.0.0"}}},
                "shared": {"version": "2.0.0"},
                "@s/scoped": {"version": "1.0.0", "dependencies": {"shared": {"version": "2.0.0"}}},
            }}
            got = {(n, v): os.path.relpath(p, d) for n, v, p in gen.npm_packages(d, tree)}
            self.assertEqual(got[("shared", "1.0.0")], "node_modules/parent/node_modules/shared")
            self.assertEqual(got[("shared", "2.0.0")], "node_modules/shared")

    def test_a_package_that_is_not_installed_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            tree = {"name": "x", "dependencies": {"gone": {"version": "1.0.0"}}}
            self.assertEqual(gen.npm_packages(d, tree), [("gone", "1.0.0", None)])


if __name__ == "__main__":
    unittest.main()
