#!/usr/bin/env python3
"""Exercise the Glosso formatter binary, including byte preservation via the C ABI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from environment import config_environment

ROOT = Path(__file__).resolve().parents[1]
GLOSSO = ROOT.parent / "glosso"
sys.path.insert(0, str(GLOSSO / "libglosso/examples"))
from binding import Library

BINARY = ROOT / "build/glofmt"
LIB = Library(os.environ.get("LIBGLOSSO_PATH", ROOT / "share/libglosso.so"))
STD = Path(os.environ.get("GLOSSO_STD", Path.home() / ".local/glosso/std"))


def significant(source):
    scan = LIB.lex(source)
    assert scan["success"], scan["diagnostics"]
    return [(t["kind"], source[t["range"]["start"]["byte"]:t["range"]["end"]["byte"]])
            for t in scan["tokens"] if t["kind"] not in ("Whitespace", "Eof")]


class Formatter(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="glofmt-config-")
        self.addCleanup(directory.cleanup)
        self.environment, self.config_path = config_environment(directory.name)

    def run_fmt(self, source, *args):
        return subprocess.run([str(BINARY), *args], input=source,
                              env=self.environment, capture_output=True, timeout=30)

    def write_config(self, text):
        if self.config_path is None:
            self.skipTest("Windows Known Folders cannot be redirected by environment")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(text, encoding="utf-8")

    def assert_format(self, source, expected, *args, preserves_tokens=True):
        if isinstance(source, str):
            source = source.encode()
        if isinstance(expected, str):
            expected = textwrap.dedent(expected).lstrip("\n").encode()
        result = self.run_fmt(source, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        again = self.run_fmt(result.stdout, *args)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, expected, "formatting must be idempotent")
        if preserves_tokens:
            self.assertEqual(significant(source), significant(result.stdout))
        return result.stdout

    def test_prefix_and_postfix_expressions(self):
        self.assert_format(
            'main::(){a:=-1;b:=!ready;c:=~bits;d:=&value;e:=*ptr;f:=foo.bar[0];g:=factory()(x)[i].field?;}',
            '''
            main :: () {
                a := -1;
                b := !ready;
                c := ~bits;
                d := &value;
                e := *ptr;
                f := foo.bar[0];
                g := factory()(x)[i].field?;
            }
            ''',
        )

    def test_operator_boundaries(self):
        self.assert_format('main::(){a:=- -x;b:=! !ready;c:=x * -2;}', '''
            main :: () {
                a := - -x;
                b := ! !ready;
                c := x * -2;
            }
            ''')

    def test_undeclared_glued_operator(self):
        self.assert_format('main::(){a:=-1;b:=x*-2;c:=foo.bar[0];x=-1;}', '''
            main :: () {
                a := -1;
                b := x * -2;
                c := foo.bar[0];
                x = -1;
            }
            ''', preserves_tokens=False)

    def test_declared_glued_operator_and_forward_use(self):
        self.assert_format("main::(){b:=x*-2;}\n'*-'::(a:s32,b:s32)->s32 #operator(left,18){return a*b;}", '''
            main :: () {
                b := x *- 2;
            }
            '*-' :: (a: s32, b: s32) -> s32 #operator(left, 18) {
                return a * b;
            }
            ''')

    def test_custom_prefix_and_suffix(self):
        self.assert_format("'^^'::(a:s32)->s32 #operator(prefix){return a;}\n'++'::(a:s32)->s32 #operator(suffix){return a;}\nmain::(){x:=^^value;y:=value++;}", '''
            '^^' :: (a: s32) -> s32 #operator(prefix) {
                return a;
            }
            '++' :: (a: s32) -> s32 #operator(suffix) {
                return a;
            }
            main :: () {
                x := ^^value;
                y := value++;
            }
            ''')

    def test_imported_operator_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ops.glo").write_text(
                '#export "./more.glo";\n' +
                "'*-'::(a:s32,b:s32)->s32 #operator(left,18){return a*b;}\n")
            (root / "more.glo").write_text('#export "./ops.glo";\n')
            source = '#import "./ops.glo";\nmain::(){b:=x*-2;}\n'
            path = root / "source.glo"
            path.write_text(source)
            expected = b'#import "./ops.glo";\nmain :: () {\n    b := x *- 2;\n}\n'
            result = self.run_fmt(b"", "--stdout", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
            self.assertEqual(significant(source.encode()), significant(result.stdout))
            path.write_bytes(result.stdout)
            self.assertEqual(self.run_fmt(b"", "--check", str(path)).returncode, 0)
            unsaved = self.run_fmt(source.encode(), f"--stdin-filepath={path}")
            self.assertEqual(unsaved.returncode, 0, unsaved.stderr)
            self.assertEqual(unsaved.stdout, expected)

    def test_unresolved_or_generated_operators_are_not_split(self):
        for prefix in ['#import "Missing_Module";', '#insert generated;', '#load_module path;']:
            with self.subTest(prefix=prefix):
                self.assert_format(prefix + '\nmain::(){b:=x*-2;}',
                                   prefix + '\nmain :: () {\n    b := x *- 2;\n}\n')

    def test_collections_and_trailing_commas(self):
        self.assert_format('main::(){p:=Point.{.x=1,.y=2};a:=.[1,2,3];q:=Point.{.x=1,.y=2,};f(a,b,);}', '''
            main :: () {
                p := Point.{ .x = 1, .y = 2 };
                a := .[1, 2, 3];
                q := Point.{
                    .x = 1,
                    .y = 2,
                };
                f(
                    a,
                    b,
                );
            }
            ''')

    def test_width_and_nested_blocks_in_calls(self):
        self.assert_format('main::(){result:=combine(first_argument,second_argument);each(values,(){a();b();});}', '''
            main :: () {
                result := combine(
                    first_argument,
                    second_argument
                );
                each(
                    values,
                    () {
                        a();
                        b();
                    }
                );
            }
            ''', '--width=40')

    def test_types_and_ranges(self):
        self.assert_format('main::(){p:*?const s32;xs:[]const u8;r:E!T;ys:[..]s32;a:=xs[1..=3];b:=xs[..];}', '''
            main :: () {
                p: *?const s32;
                xs: []const u8;
                r: E!T;
                ys: [..]s32;
                a := xs[1..=3];
                b := xs[..];
            }
            ''')
        self.assert_format('a:=..3;b:=..=4;c:=1..;d:=items[..-1];',
                           'a := ..3;\nb := ..=4;\nc := 1..;\nd := items[..-1];\n')

    def test_lambda_parameters_and_match_arms(self):
        self.assert_format('main::(){f:=|x,y|x+y;g:=||42;if x=={case 0,1;return -1;else;return 0;}}', '''
            main :: () {
                f := |x, y| x + y;
                g := || 42;
                if x == {
                    case 0, 1;
                        return -1;
                    else;
                        return 0;
                }
            }
            ''')

    def test_standard_module_operator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Ops.glo').write_text(
                "'*-'::(a:s32,b:s32)->s32 #operator(left,18){return a*b;}\n"
                "'+'::(a:s32)->s32 #operator(prefix){return a;}")
            self.assert_format('#import "Ops";\nmain::(){b:=x*-2;}',
                               '#import "Ops";\nmain :: () {\n    b := x *- 2;\n}\n',
                               f'--std={root}')
            self.assert_format('#import "Ops";\nmain::(){b:=+2;}',
                               '#import "Ops";\nmain :: () {\n    b := +2;\n}\n',
                               f'--std={root}')

    def test_comments_and_blank_lines(self):
        self.assert_format('main::(){\nx:=1; // keep here  \n\n\n// heading\ny:=2;/* keep this too */\n}\n',
                           'main :: () {\n    x := 1; // keep here  \n\n    // heading\n    y := 2; /* keep this too */\n}\n')

    def test_inline_assembly_indentation(self):
        source = (b'main::(){#asm{\n            \\\\cpuid\n'
                  b'leaf:inout(fixed(eax));clobber:ebx,ecx,edx,.Memory;}}')
        for options, indent in [((), b'    '), (('--indent=2',), b'  '), (('--tabs',), b'\t')]:
            with self.subTest(options=options):
                self.assert_format(
                    source,
                    b'main :: () {\n' + indent + b'#asm {\n' +
                    indent * 2 + b'\\\\cpuid\n' +
                    indent * 2 + b'leaf: inout(fixed(eax));\n' +
                    indent * 2 + b'clobber: ebx, ecx, edx, .Memory;\n' +
                    indent + b'}\n}\n', *options)

    def test_inline_assembly_lines_and_multiline_strings(self):
        self.assert_format(
            b'main::(){if ready{#asm /* template */ {\n// instructions\n'
            b'\\\\mov {value}, {value}\n\\\\  nop  \t\n\\\\\n'
            b'value:inout(.Register);}\ntext:=#string\n\\\\  raw text  \t\n;}}',
            b'main :: () {\n    if ready {\n        #asm /* template */ {\n'
            b'            // instructions\n'
            b'            \\\\mov {value}, {value}\n'
            b'            \\\\  nop  \t\n'
            b'            \\\\\n'
            b'            value: inout(.Register);\n'
            b'        }\n        text := #string\n'
            b'            \\\\  raw text  \t\n        ;\n    }\n}\n')

    def test_format_directives_preserve_region(self):
        region = b'  matrix:=.[1,  2,\n           3,4];  \n\n\n'
        source = (b'main::(){before:=0;\n// glofmt: off\n' + region +
                  b'    // glofmt: on\nafter:=1;}')
        expected = (b'main :: () {\n    before := 0;\n    // glofmt: off\n' + region +
                    b'    // glofmt: on\n    after := 1;\n}\n')
        self.assert_format(source, expected)

    def test_format_off_preserves_through_eof(self):
        for tail in [b'', b'\n', b'\n\n\n', b'\r\n\tx:=  1;  \r\n\r\n', b'\nx:=1; \t']:
            with self.subTest(tail=tail):
                self.assert_format(b'before:=0;\n// glofmt: off' + tail,
                                   b'before := 0;\n// glofmt: off' + tail)
        source = b'\xef\xbb\xbf// glofmt: off\r\nmain::(){x:=1;}\t '
        self.assert_format(source, source)
        self.assertEqual(self.run_fmt(source, '--check').returncode, 0)

    def test_format_directives_do_not_nest(self):
        self.assert_format(
            b'// glofmt: on\na:=1;\n// glofmt: off\nb:=2;\n'
            b'// glofmt: off\nc:=3;\n// glofmt: on\nd:=4;\n'
            b'// glofmt: off\ne:=5;\n// glofmt: on\nf:=6;',
            b'// glofmt: on\na := 1;\n// glofmt: off\nb:=2;\n'
            b'// glofmt: off\nc:=3;\n// glofmt: on\nd := 4;\n'
            b'// glofmt: off\ne:=5;\n// glofmt: on\nf := 6;\n')

    def test_format_directives_whitespace_and_trailing_comments(self):
        self.assert_format(
            'before:=0; //\t glofmt: off \t\r\n'
            '\t이름:=  "한";  \r\n\r\n\r\n'
            'inside:=1; //glofmt: on\t\r\nafter:=2;',
            ('before := 0; //\t glofmt: off \t\r\n'
             '\t이름:=  "한";  \r\n\r\n\r\n'
             'inside:=1; //glofmt: on\t\r\nafter := 2;\n').encode())

    def test_format_directives_only_match_exact_line_comments(self):
        for comment in [b'/* glofmt: off */', b'/// glofmt: off', b'//! glofmt: off',
                        b'// glofmt: off trailing', b'// glofmt: offline',
                        b'// glofmt: OFF', b'// zig fmt: off', b'// mentions // glofmt: off']:
            with self.subTest(comment=comment):
                self.assert_format(comment + b'\nx:=1;', comment + b'\nx := 1;\n')
        self.assert_format('text:="// glofmt: off";x:=1;',
                           'text := "// glofmt: off";\nx := 1;\n')
        region = (b'// glofmt: off\ntext:="// glofmt: on";\n'
                  b'/* // glofmt: on */\n/// glofmt: on\n'
                  b'// glofmt: on trailing\n'
                  b'raw:=#string\n  \\\\// glofmt: on\n;\nx:=1;\n')
        self.assert_format(region + b'// glofmt: on\ny:=2;',
                           region + b'// glofmt: on\ny := 2;\n')

    def test_format_directives_track_delimiters_and_match_arms(self):
        cases = [
            (b'main::(){\n    // glofmt: off\nif(ok){\n'
             b'        // glofmt: on\nx:=1;}}',
             b'main :: () {\n    // glofmt: off\nif(ok){\n'
             b'        // glofmt: on\n        x := 1;\n    }\n}\n'),
            (b'main::(){if(ok){\n        // glofmt: off\nx:=1;}\n'
             b'    // glofmt: on\ny:=2;}',
             b'main :: () {\n    if (ok) {\n        // glofmt: off\nx:=1;}\n'
             b'    // glofmt: on\n    y := 2;\n}\n'),
            (b'main::(){\n    // glofmt: off\nxs:=.[1,\n'
             b'        // glofmt: on\n2,3,];}',
             b'main :: () {\n    // glofmt: off\nxs:=.[1,\n'
             b'        // glofmt: on\n        2,\n        3,\n    ];\n}\n'),
            (b'main::(){if x=={\n        // glofmt: off\ncase 0;\nx:=1;\n'
             b'            // glofmt: on\ny:=2;else;z:=3;}}',
             b'main :: () {\n    if x == {\n        // glofmt: off\ncase 0;\nx:=1;\n'
             b'            // glofmt: on\n            y := 2;\n'
             b'        else;\n            z := 3;\n    }\n}\n'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assert_format(source, expected)

    def test_format_directives_and_operator_repair(self):
        self.assert_format(
            b'before:=x*-2;\n// glofmt: off\ninside:=x*-2;\n// glofmt: on\nafter:=x*-2;',
            b'before := x * -2;\n// glofmt: off\ninside:=x*-2;\n// glofmt: on\nafter := x * -2;\n',
            preserves_tokens=False)
        declaration = (b'// glofmt: off\n'
                       b"'*-'::(a:s32,b:s32)->s32 #operator(left,18){return a*b;}\n"
                       b'// glofmt: on\n')
        self.assert_format(b'before:=x*-2;\n' + declaration + b'after:=x*-2;',
                           b'before := x *- 2;\n' + declaration + b'after := x *- 2;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Ops.glo').write_text(
                "'*-'::(a:s32,b:s32)->s32 #operator(left,18){return a*b;}\n")
            prefix = b'// glofmt: off\n#import "Ops";\n// glofmt: on\n'
            self.assert_format(prefix + b'after:=x*-2;', prefix + b'after := x *- 2;\n',
                               f'--std={root}')

    def test_format_directives_respect_options_outside_region(self):
        region = b'\t  xs:=.[first_argument,second_argument,];  \n'
        for options, indent in [(('--tabs',), b'\t'), (('--indent=2',), b'  ')]:
            with self.subTest(options=options):
                self.assert_format(
                    b'main::(){\n// glofmt: off\n' + region +
                    indent + b'// glofmt: on\nf(a,b,);}',
                    b'main :: () {\n' + indent + b'// glofmt: off\n' + region +
                    indent + b'// glofmt: on\n' + indent + b'f(\n' +
                    indent * 2 + b'a,\n' + indent * 2 + b'b,\n' + indent + b');\n}\n',
                    '--width=20', *options)

    def test_format_directives_file_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.glo'
            source = b'// glofmt: off\nx:=1;\n// glofmt: on\ny:=2;'
            expected = b'// glofmt: off\nx:=1;\n// glofmt: on\ny := 2;\n'
            path.write_bytes(source)
            preview = self.run_fmt(b'', '--stdout', str(path))
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(preview.stdout, expected)
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(self.run_fmt(b'', '--check', str(path)).returncode, 1)
            self.assertEqual(path.read_bytes(), source)
            written = self.run_fmt(b'', str(path))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(self.run_fmt(b'', '--check', str(path)).returncode, 0)

    def test_empty_blocks_and_else(self):
        self.assert_format('main::(){if(ok){}\nelse{f();}}', '''
            main :: () {
                if (ok) {} else {
                    f();
                }
            }
            ''')

    def test_continuations_and_compact_indexing(self):
        self.assert_format('main::(){\nif first\n&&second{run();}\nif ready\nreturn -1;\nx:=some_very_long_name.field[0];\n}', '''
            main :: () {
                if first
                    && second {
                    run();
                }
                if ready
                    return -1;
                x := some_very_long_name.field[0];
            }
            ''', '--width=30')

    def test_file_default_write_and_explicit_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.glo'
            original = b'main::(){a:=-1;c:=foo.bar[0];}'
            expected = b'main :: () {\n    a := -1;\n    c := foo.bar[0];\n}\n'
            path.write_bytes(original)
            path.chmod(0o640)
            preview = self.run_fmt(b'', '--stdout', str(path))
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(preview.stdout, expected)
            self.assertEqual(path.read_bytes(), original)
            check = self.run_fmt(b'', '--check', str(path))
            self.assertEqual(check.returncode, 1)
            self.assertEqual(check.stdout, b'')
            self.assertEqual(path.read_bytes(), original)
            written = self.run_fmt(b'', str(path))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(written.stdout, b'')
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(self.run_fmt(b'', '--check', str(path)).returncode, 0)
            for options in [('--stdout', '--write'), ('--stdout', '--check')]:
                self.assertEqual(self.run_fmt(b'', *options, str(path)).returncode, 2)
            invalid = b'main::(){x:="unterminated'
            path.write_bytes(invalid)
            self.assertEqual(self.run_fmt(b'', str(path)).returncode, 2)
            self.assertEqual(path.read_bytes(), invalid)

    def test_mismatched_delimiters_are_preserved(self):
        for source in [b'main::(){x:=1;', b'main::(){x:=([)];}', b'}{']:
            with self.subTest(source=source):
                self.assert_format(source, source)

    def test_width_and_indent_arguments(self):
        self.assert_format('main::(){f(a,b,);}', 'main :: () {\n  f(\n    a,\n    b,\n  );\n}\n', '--indent=2')
        self.assert_format('main::(){f(a,b,);}', b'main :: () {\n\tf(\n\t\ta,\n\t\tb,\n\t);\n}\n', '--tabs')
        for arg in ['--width=19', '--width=1001', '--width=', '--width=no', '--std=', '--unknown', '--stdin-filepath=']:
            with self.subTest(arg=arg):
                self.assertEqual(self.run_fmt(b'', arg).returncode, 2)

    def test_config_width_and_cli_override(self):
        source = 'main::(){result:=combine(first_argument,second_argument);}'
        compact = 'main :: () {\n    result := combine(first_argument, second_argument);\n}\n'
        expanded = '''
            main :: () {
                result := combine(
                    first_argument,
                    second_argument
                );
            }
            '''
        self.assert_format(source, compact)
        for contents in ['width = 40\n', '# preferred columns\n"width" = +40 # comment\n',
                         'width = 4_0\n', 'width = 0x28\n', 'width = 0o50\n',
                         'width = 0b101000\n', 'width = 20\n']:
            with self.subTest(contents=contents):
                self.write_config(contents)
                self.assert_format(source, expanded)
                self.assert_format(source, compact, '--width=80')
                # An explicit value equal to the default must override too.
                self.assert_format(source, compact, '--width=100')
        self.write_config('width = 1000\n')
        self.assert_format(source, compact)
        self.assert_format(source, expanded, '--width=40')

    def test_config_missing_width_uses_default(self):
        source = b'main::(){result:=combine(first_argument,second_argument);}'
        default = self.run_fmt(source, '--width=100').stdout
        for contents in ['', '# no settings\n', 'unrelated = true\n',
                         '[unrelated]\nwidth = 20\n']:
            with self.subTest(contents=contents):
                self.write_config(contents)
                self.assert_format(source, default)

    def test_config_width_file_modes(self):
        self.write_config('width = 40\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.glo'
            source = b'main::(){result:=combine(first_argument,second_argument);}'
            expected = (b'main :: () {\n    result := combine(\n'
                        b'        first_argument,\n        second_argument\n    );\n}\n')
            path.write_bytes(source)
            preview = self.run_fmt(b'', '--stdout', str(path))
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(preview.stdout, expected)
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(self.run_fmt(b'', '--check', str(path)).returncode, 1)
            written = self.run_fmt(b'', str(path))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(written.stdout, b'')
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(self.run_fmt(b'', '--check', str(path)).returncode, 0)
            self.assertEqual(self.run_fmt(expected, '--check').returncode, 0)

    @unittest.skipUnless(sys.platform.startswith(('linux', 'freebsd')), 'XDG config lookup')
    def test_config_xdg_and_home_fallback(self):
        self.write_config('width = 40\n')
        source = b'main::(){result:=combine(first_argument,second_argument);}'
        expanded = self.run_fmt(source).stdout
        compact = self.run_fmt(source, '--width=100').stdout
        self.assertNotEqual(expanded, compact)
        fallback = Path(self.environment['HOME']) / '.config/glofmt/config.toml'
        fallback.parent.mkdir(parents=True)
        fallback.write_text('width = 100\n')
        # The absolute XDG setting takes priority over the home fallback.
        self.assert_format(source, expanded)
        for value in [None, '', 'relative/config']:
            with self.subTest(value=value):
                if value is None:
                    self.environment.pop('XDG_CONFIG_HOME', None)
                else:
                    self.environment['XDG_CONFIG_HOME'] = value
                self.assert_format(source, compact)
        # Path lookup must also work with spaces and non-ASCII characters.
        base = fallback.parents[2] / 'config space 한'
        self.environment['XDG_CONFIG_HOME'] = str(base)
        self.config_path = base / 'glofmt/config.toml'
        self.write_config('width = 40\n')
        self.assert_format(source, expanded)

    def test_invalid_config_does_not_write_input(self):
        bad_values = ['19', '1001', '-20', '0', '40.0', '"40"', 'true', '[40]',
                      '{}', '9223372036854775808', '9999999999999999999999999999999999999999']
        bad_configs = [f'width = {value}\n' for value in bad_values]
        bad_configs += ['width =\n', 'width = 40\nwidth = 80\n', '[broken\n']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.glo'
            source = b'main::(){x:=1;}'
            path.write_bytes(source)
            for contents in bad_configs:
                with self.subTest(contents=contents):
                    self.write_config(contents)
                    for args in [(), ('--width=80',), ('--check',), ('--stdout',)]:
                        result = self.run_fmt(b'', *args, str(path))
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertEqual(result.stdout, b'')
                        self.assertIn(str(self.config_path).encode(), result.stderr)
                        self.assertEqual(path.read_bytes(), source)
            for arg in ['--help', '--version']:
                result = self.run_fmt(b'', arg)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b'')

    def test_config_read_errors(self):
        self.write_config('width = 40\n')
        self.config_path.unlink()
        self.config_path.mkdir()
        failed = self.run_fmt(b'main::(){}')
        self.assertEqual(failed.returncode, 2, failed.stderr)
        self.assertEqual(failed.stdout, b'')
        self.assertIn(str(self.config_path).encode(), failed.stderr)
        self.config_path.rmdir()
        self.write_config('width = 40\n')
        if hasattr(os, 'geteuid') and os.geteuid() != 0:
            self.config_path.chmod(0o000)
            try:
                failed = self.run_fmt(b'main::(){}')
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertEqual(failed.stdout, b'')
                self.assertIn(b'permission denied', failed.stderr)
            finally:
                self.config_path.chmod(0o600)

    def test_operator_repair_passes_compiler_analysis(self):
        cases = [
            (b'answer::6*-2;#comptime { #assert answer == -12; }', b'6 * -2'),
            (b"Fancy::typeclass(L:type,R:type){Output::type;'*-'::(a:L,b:R)->Output #operator(left,18);}\n"
             b"(ssize,ssize):::instance Fancy{Output::ssize;'*-'::(a:ssize,b:ssize)->Output{return a*10+b;}}\n"
             b'answer::6*-2;#comptime { #assert answer == 62; }', b'6 *- 2'),
        ]
        for source, expression in cases:
            with self.subTest(expression=expression):
                formatted = self.run_fmt(source)
                self.assertEqual(formatted.returncode, 0, formatted.stderr)
                self.assertIn(expression, formatted.stdout)
                with LIB.context(STD) as context:
                    context.file('/tmp/glofmt-operator-repair.glo', formatted.stdout)
                    result, _, _ = context.analyze('/tmp/glofmt-operator-repair.glo', flags=8)
                self.assertTrue(result['success'], result.get('diagnostics'))

    def test_layout_and_idempotence(self):
        original = b"main::(){x:=1;y:=x+2;}"
        formatted = self.run_fmt(original)
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        self.assertEqual(formatted.stdout, b"main :: () {\n    x := 1;\n    y := x + 2;\n}\n")
        self.assertEqual(self.run_fmt(formatted.stdout).stdout, formatted.stdout)
        self.assertEqual(self.run_fmt(original, "--check").returncode, 1)
        self.assertEqual(self.run_fmt(formatted.stdout, "--check").returncode, 0)
        self.assertEqual(self.run_fmt(original, "--tabs").stdout.count(b"\n\t"), 2)

    def test_tokens_comments_unicode_and_literals(self):
        cases = [
            '\ufeff// 😀한\r\nmain::(){이름:="x\\n { ; }";/* outer /* inner */ comment */}',
            'main::(){a:=#matrix .[1,2;3,4]; print("{0:x} {{literal}}",65);}',
            'raw :: #string\n  \\\\  { raw; // unchanged }\n;\n',
            "'custom'::(a:s32,b:s32)->s32{return a+b;}\n",
        ]
        for source in cases:
            original = source.encode()
            with self.subTest(source=source):
                formatted = self.run_fmt(original)
                self.assertEqual(formatted.returncode, 0, formatted.stderr)
                self.assertEqual(significant(original), significant(formatted.stdout))
                self.assertEqual(self.run_fmt(formatted.stdout).stdout, formatted.stdout)

    def test_write_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.glo"
            path.write_bytes(b"main::(){x:=1;}")
            self.assertEqual(self.run_fmt(b"", "--write", str(path)).returncode, 0)
            self.assertEqual(path.read_bytes(), b"main :: () {\n    x := 1;\n}\n")
            path.write_bytes(b'main::(){x:="unterminated')
            before = path.read_bytes()
            failed = self.run_fmt(b"", "--write", str(path))
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(self.run_fmt(b"\xff").returncode, 2)
            self.assertEqual(self.run_fmt(b"", "--indent=0").returncode, 2)

    def test_never_executes_comptime(self):
        source = b'#comptime { #compile_error "do not execute formatter input"; }'
        result = self.run_fmt(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(significant(source), significant(result.stdout))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--formatter", type=Path, default=BINARY)
    args, rest = parser.parse_known_args()
    BINARY = args.formatter.resolve()
    unittest.main(argv=[sys.argv[0], *rest])
