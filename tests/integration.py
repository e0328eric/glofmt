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

    def test_variadic_parameters_and_argument_expansion(self):
        self.assert_format(
            'VgaWriter ::: #public {\n'
            '    print :: (*#self, #comptime format: str, args: ... any) -> Io_Error!void {\n'
            '        #self.write_format(format, args ...)?;\n'
            '        return .Ok;\n'
            '    }\n'
            '}\n',
            '''
            VgaWriter ::: #public {
                print :: (*#self, #comptime format: str, args: ...any) -> Io_Error!void {
                    #self.write_format(format, args...)?;
                    return .Ok;
                }
            }
            ''',
        )
        cases = [
            ('run::(args: ... $T){}', 'run :: (args: ...$T) {}\n'),
            ('run::(args: ... (Show && Debug)){}',
             'run :: (args: ...(Show && Debug)) {}\n'),
            ('run::(args: ...){}', 'run :: (args: ...) {}\n'),
            ('run::(fixed:s32, ...){}', 'run :: (fixed: s32, ...) {}\n'),
            ('call((args) ...,object.args ...,get_args() ...);',
             'call((args)..., object.args..., get_args()...);\n'),
            ('run::(args: ... any,){call(args ...,);}',
             'run :: (\n    args: ...any,\n) {\n    call(\n        args...,\n    );\n}\n'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assert_format(source, expected)

    def test_variadic_spacing_preserves_comments(self):
        self.assert_format(
            'run::(args: ... /* type */ any){call(args /* expand */ ...);}',
            'run :: (args: ... /* type */ any) {\n'
            '    call(args /* expand */ ...);\n'
            '}\n',
        )

    def test_directive_modifiers(self):
        cases = [
            ('run::()#inline,always{}', 'run :: () #inline,always {}\n'),
            ('run::()#inline,never{}', 'run :: () #inline,never {}\n'),
            ('run::()#expand,expression{}', 'run :: () #expand,expression {}\n'),
            ('#public,unit value::1;', '#public,unit value :: 1;\n'),
            ('#public,to("Friend","Other")value::1;',
             '#public,to("Friend", "Other") value :: 1;\n'),
            ('lib::#library,system "c";', 'lib :: #library,system "c";\n'),
            ('lib::#library,static "lib.a";', 'lib :: #library,static "lib.a";\n'),
            ('lib::#library,dyn "lib.so";', 'lib :: #library,dyn "lib.so";\n'),
            ('Meters::distinct f64 #derive,all;', 'Meters :: distinct f64 #derive,all;\n'),
            ('#insert,scope() code;', '#insert,scope() code;\n'),
        ]
        for source, expected in cases:
            for spaced in [source, source.replace(',', ' ,  ')]:
                with self.subTest(source=spaced):
                    self.assert_format(spaced, expected)

    def test_directive_modifiers_in_multiline_lists(self):
        self.assert_format('each(values,()#inline,always{work();},);', '''
            each(
                values,
                () #inline,always {
                    work();
                },
            );
            ''')
        for modifiers in ['oneline', 'escape', 'oneline,escape', 'escape,oneline']:
            with self.subTest(modifiers=modifiers):
                self.assert_format(
                    'use(#string , ' + modifiers.replace(',', ' , ') + '\n'
                    '\\\\first\\t\n\\\\second\n,value);',
                    'use(\n    #string,' + modifiers + '\n'
                    '        \\\\first\\t\n        \\\\second\n    ,\n    value\n);\n',
                )

    def test_directive_modifiers_preserve_comments(self):
        self.assert_format(
            'run::()#inline/* mode */,/* reason */always{}\n'
            'other::()#inline, // reason\nnever{}',
            'run :: () #inline /* mode */, /* reason */ always {}\n'
            'other :: () #inline, // reason\nnever {}\n',
        )

    def test_directive_values_and_argument_commas(self):
        self.assert_format(
            'call(#self,value,#context,#source_location,#undefined);'
            'Types::(#Self_Type,#Context,#Never);'
            "'+'::(a:T,b:T)->T #operator(left,18){}",
            'call(#self, value, #context, #source_location, #undefined);\n'
            'Types :: (#Self_Type, #Context, #Never);\n'
            "'+' :: (a: T, b: T) -> T #operator(left, 18) {}\n",
        )

    def test_directive_enum_literal_operand(self):
        expected = '''
            main :: () {
                #pattern .Ok(list) := read_directory(path) else {
                    return 0;
                };
                x := #self.value + #source_location.line;
            }
            '''
        for spacing in ['#pattern .Ok', '#pattern.Ok']:
            with self.subTest(spacing=spacing):
                self.assert_format(
                    'main::(){' + spacing + '(list):=read_directory(path)else{return 0;};'
                    'x:=#self.value+#source_location.line;}',
                    expected,
                )

    def test_visibility_groups(self):
        for directive in ['#public', '#private', '#public,unit', '#public,to("Friend","Other")']:
            with self.subTest(directive=directive):
                self.assert_format(
                    directive + '{VGA_BUFFER_WIDTH::80;\n\n'
                    '// One explicit byte for the two colors.\n'
                    'VgaColor::struct #packed{bits:u8;}\n\n'
                    'VgaChar::struct #c_call{char:u8;color:VgaColor;}'
                    '} // END visibility\nafter::1;',
                    directive.replace('"Friend","Other"', '"Friend", "Other"') + ' {\n\n'
                    'VGA_BUFFER_WIDTH :: 80;\n\n'
                    '// One explicit byte for the two colors.\n'
                    'VgaColor :: struct #packed {\n    bits: u8;\n}\n\n'
                    'VgaChar :: struct #c_call {\n    char: u8;\n    color: VgaColor;\n}\n\n'
                    '} // END visibility\nafter :: 1;\n',
                )

    def test_nested_visibility_groups_and_indentation(self):
        source = ('#public{Container::struct{before:u8;'
                  '#private{hidden:u8;#public{shown:u8;}tail:u8;}'
                  'after:u8;}run::(){f(a,b,);}}outside::1;')
        expected = '''\
#public {

Container :: struct {
    before: u8;
    #private {

    hidden: u8;
    #public {

    shown: u8;

    }
    tail: u8;

    }
    after: u8;
}
run :: () {
    f(
        a,
        b,
    );
}

}
outside :: 1;
'''
        for options, indent in [((), '    '), (('--indent=2',), '  '), (('--tabs',), '\t')]:
            with self.subTest(options=options):
                self.assert_format(source, expected.replace('    ', indent), *options)

    def test_visibility_group_comments_and_empty_groups(self):
        self.assert_format(
            '#public /* audience */, /* unit */ unit /* group */ { // API\n\n\n'
            '/// docs\nvalue::1; // trailing\n\n\n} // END #public\n'
            '#private{/* comment only */}\n#public{\n\n}\n#private{}',
            '#public /* audience */, /* unit */ unit /* group */ { // API\n\n'
            '/// docs\nvalue :: 1; // trailing\n\n} // END #public\n'
            '#private { /* comment only */\n\n}\n#public {}\n#private {}\n',
        )
        self.assert_format(
            '#public,to(/* friend */ "Friend","Other"){value::1;}',
            '#public,to( /* friend */ "Friend", "Other") {\n\nvalue :: 1;\n\n}\n',
        )

    def test_visibility_prefixes_keep_declaration_bodies_indented(self):
        self.assert_format(
            '#public Thing::struct{value:u8;}#private run::(){work();}'
            'Thing:::#public,unit{value::(#self)->u8{return #self.value;}}'
            '$T:::#public where Show(T){show::(#self){work();}}',
            '''
            #public Thing :: struct {
                value: u8;
            }
            #private run :: () {
                work();
            }
            Thing ::: #public,unit {
                value :: (#self) -> u8 {
                    return #self.value;
                }
            }
            $T ::: #public where Show(T) {
                show :: (#self) {
                    work();
                }
            }
            ''',
        )

    def test_visibility_groups_respect_width(self):
        self.assert_format(
            '#public{value::combine(first_argument,second_argument);}',
            '#public {\n\nvalue :: combine(first_argument, second_argument);\n\n}\n',
            '--width=50',
        )
        self.assert_format(
            '#public,to("Friend","Other"){value::combine(first_argument,second_argument);}',
            '#public,to(\n    "Friend",\n    "Other"\n) {\n\n'
            'value :: combine(\n    first_argument,\n    second_argument\n);\n\n}\n',
            '--width=20',
        )

    def test_cast_prefix_operands(self):
        self.assert_format(
            'function:=cast(Generated_Function)&code;#assert cast(usize)&code%64==0;',
            'function := cast(Generated_Function) &code;\n'
            '#assert cast(usize) &code % 64 == 0;\n',
        )
        for operator in ['&', '&&', '*', '**', '-', '!', '~']:
            with self.subTest(operator=operator):
                self.assert_format(
                    f'result:=cast(T) {operator} value;',
                    f'result := cast(T) {operator}value;\n',
                )

    def test_cast_type_delimiters(self):
        self.assert_format(
            'a:=cast(*const Pair(u8,u16))&value;'
            'b:=cast(usize)cast(*void)&value;'
            'c:=cast /* target */ (T) /* operand */ & value;'
            'd:=cast(T)! !ready;',
            '''
            a := cast(*const Pair(u8, u16)) &value;
            b := cast(usize) cast(*void) &value;
            c := cast /* target */ (T) /* operand */ &value;
            d := cast(T) ! !ready;
            ''',
        )

    def test_cast_operands_and_binary_continuations(self):
        self.assert_format(
            'a:=cast(usize)(&value)&mask;b:=(cast(usize)&value)&mask;'
            'c:=cast(s32)(value)*scale;d:=cast(usize)address&mask;'
            'e:=call(value)&mask;f:=(value)-offset;g:=cast(s32)-value-offset;',
            '''
            a := cast(usize)(&value) & mask;
            b := (cast(usize) &value) & mask;
            c := cast(s32)(value) * scale;
            d := cast(usize) address & mask;
            e := call(value) & mask;
            f := (value) - offset;
            g := cast(s32) -value - offset;
            ''',
        )

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

    def test_type_declaration_heads_after_blocks(self):
        heads = [
            ('[] const u8', '[]const u8'),
            ('[] u8', '[]u8'),
            ('* [..] u8', '*[..]u8'),
            ('* const [..] u8', '*const [..]u8'),
            ('[4] u8', '[4]u8'),
            ('[*] const volatile * u8', '[*]const volatile *u8'),
            ('? [] const u8', '?[]const u8'),
            ('[] * $ T', '[]*$T'),
            ('* * const [4] u8', '* *const [4]u8'),
            ('E ! ? [..] u8', 'E!?[..]u8'),
            ('([] const u8, * const [..] u8)', '([]const u8, *const [..]u8)'),
        ]
        for source, expected in heads:
            for gap in ['', '\n', ' // keep\n', '\n\n/// docs\n']:
                with self.subTest(head=source, gap=gap):
                    self.assert_format(
                        'Header::struct{}' + gap + source + ':::instance Source{}',
                        'Header :: struct {}' + (gap or '\n') +
                        expected + ' ::: instance Source {}\n',
                    )

    def test_nested_type_declarations(self):
        self.assert_format('''#public {
            Source::typeclass(T:type){}
            [] const u8:::instance Source{view::(source:[]const u8)->[]const u8{return source;}}
            * const [..] u8:::instance Source{view::(source:*const [..]u8)->[]const u8{return source.*.view();}}
            [] * $ T:::#public{first::(#self)->*T{return #self[0];}}
        }''', '''
            #public {

            Source :: typeclass(T: type) {}
            []const u8 ::: instance Source {
                view :: (source: []const u8) -> []const u8 {
                    return source;
                }
            }
            *const [..]u8 ::: instance Source {
                view :: (source: *const [..]u8) -> []const u8 {
                    return source.*.view();
                }
            }
            []*$T ::: #public {
                first :: (#self) -> *T {
                    return #self[0];
                }
            }

            }
            ''')

    def test_qualified_nested_type_values(self):
        self.assert_format(
            'A::* const [..] u8;B::** volatile [] const * u8;'
            'C::[*] const volatile * const [4] u8;'
            'main::(){use(*const []u8,[]volatile *u8);p:*volatile []const u8;}',
            '''
            A :: *const [..]u8;
            B :: **volatile []const *u8;
            C :: [*]const volatile *const [4]u8;
            main :: () {
                use(*const []u8, []volatile *u8);
                p: *volatile []const u8;
            }
            ''')

    def test_expression_continuations_after_groups(self):
        self.assert_format(
            'main::(){a:=values[index]*scale;b:=Point.{.x=1}*scale;'
            'c:=#try{operation()}*scale;d:=(){return 1;}();'
            'e:=Point.{.x=1}[index];f:=.[1,2][index];}',
            '''
            main :: () {
                a := values[index] * scale;
                b := Point.{ .x = 1 } * scale;
                c := #try {
                    operation()
                } * scale;
                d := () {
                    return 1;
                }();
                e := Point.{ .x = 1 }[index];
                f := .[1, 2][index];
            }
            ''')

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

    def test_case_arms_align_with_owner(self):
        self.assert_format(
            'main::(){if arg=={case "-d","--delay";delay=1.0;case "-w";windowed=true;else;}'
            '#if OS=={case .Linux;x:=1;else;x:=2;}}#if OS=={case .Linux;#import "Linux";else;}',
            '''
            main :: () {
                if arg == {
                case "-d", "--delay";
                    delay = 1.0;
                case "-w";
                    windowed = true;
                else;
                }
                #if OS == {
                case .Linux;
                    x := 1;
                else;
                    x := 2;
                }
            }
            #if OS == {
            case .Linux;
                #import "Linux";
            else;
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
            (b'main::(){if x=={\n    // glofmt: off\ncase 0;\nx:=1;\n'
             b'        // glofmt: on\ny:=2;else;z:=3;}}',
             b'main :: () {\n    if x == {\n    // glofmt: off\ncase 0;\nx:=1;\n'
             b'        // glofmt: on\n        y := 2;\n'
             b'    else;\n        z := 3;\n    }\n}\n'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assert_format(source, expected)

    def test_format_directives_track_visibility_groups(self):
        cases = [
            ('// glofmt: off\n#public{\n// glofmt: on\nvalue::1;}',
             '// glofmt: off\n#public{\n// glofmt: on\nvalue :: 1;\n\n}\n'),
            ('#public{value::1;\n// glofmt: off\n}\n// glofmt: on\nafter::2;',
             '#public {\n\nvalue :: 1;\n// glofmt: off\n}\n// glofmt: on\nafter :: 2;\n'),
            ('#public{ // glofmt: off\n  value::1;\n// glofmt: on\nafter::2;}',
             '#public { // glofmt: off\n  value::1;\n// glofmt: on\nafter :: 2;\n\n}\n'),
            ('#public{\n// glofmt: off\nThing::struct{\n    // glofmt: on\nfield:u8;}}',
             '#public {\n\n// glofmt: off\nThing::struct{\n    // glofmt: on\n    field: u8;\n}\n\n}\n'),
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

    def test_braceless_if_chains_align_when_every_arm_fits(self):
        self.assert_format(
            'main::(){if options.width>0 r.width=options.width;'
            'else if r.tty&&caps.columns>0 r.width=caps.columns;'
            'else if #pattern ?n=env_columns() r.width=n;else r.width=80;'
            'if ready x=1;else x=2;if x in set return;}',
            '''
            main :: () {
                if      options.width > 0           r.width = options.width;
                else if r.tty && caps.columns > 0   r.width = caps.columns;
                else if #pattern ?n = env_columns() r.width = n;
                else                                r.width = 80;
                if ready x = 1;
                else     x = 2;
                if x in set return;
            }
            ''')
        # Tabs indent; spaces align.
        self.assert_format('main::(){if a b();else if cc d();}',
                           'main :: () {\n\tif      a  b();\n\telse if cc d();\n}\n', '--tabs')

    def test_braceless_if_chains_break_every_body_when_too_wide(self):
        self.assert_format(
            'main::(){if options.width>0 r.width=options.width;'
            'else if #pattern ?n=env_columns_or(fallback) r.width=n;else r.width=80;'
            'if ready&&some_long_condition_name return report_failure(path,reason);'
            'if ready return;}',
            '''
            main :: () {
                if options.width > 0
                    r.width = options.width;
                else if #pattern ?n = env_columns_or(fallback)
                    r.width = n;
                else
                    r.width = 80;
                if ready && some_long_condition_name
                    return report_failure(path, reason);
                if ready return;
            }
            ''', '--width=60')

    def test_braceless_if_conditions_break_after_loosest_logical_operator(self):
        self.assert_format(
            'main::(){if alpha_value>0&&beta_value>0&&gamma_value>0 return;'
            'if a x=1;else if first_one&&second||third_value&&fourth||fifth z();'
            'if left_value&&\nright_value return;'
            'if left_value\n&&right_value return;}',
            '''
            main :: () {
                if alpha_value > 0 &&
                   beta_value > 0 && gamma_value > 0
                    return;
                if a
                    x = 1;
                else if first_one && second ||
                        third_value && fourth ||
                        fifth
                    z();
                if left_value &&
                   right_value
                    return;
                if left_value &&
                   right_value
                    return;
            }
            ''', '--width=40')
        # Tabs indent; spaces align.
        self.assert_format('main::(){if alpha_value>0&&beta_value>0&&gamma_value>0 return;}',
                           'main :: () {\n\tif alpha_value > 0 &&\n'
                           '\t   beta_value > 0 && gamma_value > 0\n\t\treturn;\n}\n',
                           '--tabs', '--width=40')

    def test_consecutive_lone_ifs_align_bodies(self):
        self.assert_format(
            'main::(){if a x=1;if bbb y=2;if cc z=3;\n\n'
            'if a x=1;\n// note\nif bbb y=2;'
            'if ready&&some_long_condition_name return fail(path,reason);'
            'if a\nx=1;if bbb y=2;\n\n'
            'if long_condition_value x=1;if c long_body_statement_here=2;'
            'if ready x=1;else x=2;if dd y=3;}',
            '''
            main :: () {
                if a   x = 1;
                if bbb y = 2;
                if cc  z = 3;

                if a x = 1;
                // note
                if bbb y = 2;
                if ready && some_long_condition_name
                    return fail(path, reason);
                if a
                    x = 1;
                if bbb y = 2;

                if long_condition_value x = 1;
                if c long_body_statement_here = 2;
                if ready x = 1;
                else     x = 2;
                if dd y = 3;
            }
            ''', '--width=40')
        self.assert_format('main::(){if a b();if cc d();}',
                           'main :: () {\n\tif a  b();\n\tif cc d();\n}\n', '--tabs')

    def test_braceless_if_chain_line_break_requests_broken_layout(self):
        self.assert_format('main::(){if a x=1;\nelse if b\ny=2;else z=3;}', '''
            main :: () {
                if a
                    x = 1;
                else if b
                    y = 2;
                else
                    z = 3;
            }
            ''')

    def test_braceless_if_chains_need_single_statement_arms(self):
        self.assert_format(
            'main::(){if a x=1;else{y();}\nif a x=1; // note\nelse y=2;\n'
            'if x=={case 1;if a b=1;else;c=2;}}',
            '''
            main :: () {
                if a x = 1;
                else {
                    y();
                }
                if a x = 1; // note
                else y = 2;
                if x == {
                case 1;
                    if a b = 1;
                else;
                    c = 2;
                }
            }
            ''')

    def test_long_method_chains_break_before_each_call(self):
        self.assert_format(
            'env::(name:str,fallback:str="")->str{'
            'return environment_get_or(name,fallback,context.temp_allocator).unwrap_or(fallback).into_temporary();'
            'sb.write_string("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").write_string("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");'
            'x:=value.field.other.first(argument_one,argument_two).second(argument_three)?.done(argument_four);'
            'short:=a.b().c();z:=environment_get_or(name,fallback,context.temp_allocator).unwrap_or(fallback);'
            'call(first_argument,builder.add(one_thing_here).add(another_thing_here).add(the_third_thing_too)'
            '.add(fourth).build());}',
            '''
            env :: (name: str, fallback: str = "") -> str {
                return environment_get_or(name, fallback, context.temp_allocator)
                    .unwrap_or(fallback)
                    .into_temporary();
                sb.write_string("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                    .write_string("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
                x := value.field.other
                    .first(argument_one, argument_two)
                    .second(argument_three)?
                    .done(argument_four);
                short := a.b().c();
                z := environment_get_or(name, fallback, context.temp_allocator).unwrap_or(fallback);
                call(
                    first_argument,
                    builder
                        .add(one_thing_here)
                        .add(another_thing_here)
                        .add(the_third_thing_too)
                        .add(fourth)
                        .build()
                );
            }
            ''')

    def test_method_chain_line_breaks_and_block_like_roots(self):
        # A break before one element in a block lays out the whole chain
        # vertically; a member access alone on its line is a continuation.
        self.assert_format(
            'main::(){\nw:=a.b()\n.c();\ny:=foo\n.bar();\n'
            'v:=environment_get_or(name,fallback,context.temp_allocator).unwrap_or(fallback).into_temporary();\n}',
            '''
            main :: () {
                w := a
                    .b()
                    .c();
                y := foo
                    .bar();
                v := environment_get_or(
                    name,
                    fallback,
                    context.temp_allocator
                )
                .unwrap_or(fallback)
                .into_temporary();
            }
            ''', '--width=60')

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

    def test_config_can_disable_braceless_if_layout(self):
        source = ('main::(){if options.width>0 r.width=options.width;'
                  'else if r.tty&&caps.columns>0 r.width=caps.columns;else r.width=80;'
                  'if a x=1;if bbb y=2;\nif a\nx=1;\n'
                  'if alpha_value>0&&beta_value>0&&gamma_value>0 return;\n'
                  'if left_value\n&&right_value return;}')
        enabled = self.run_fmt(source.encode(), '--width=40').stdout
        self.write_config('format_braceless_if = true\n')
        self.assert_format(source, enabled, '--width=40')
        # The ordinary layout keeps line breaks and neither aligns nor breaks.
        disabled = '''
            main :: () {
                if options.width > 0 r.width = options.width;
                else if r.tty && caps.columns > 0 r.width = caps.columns;
                else r.width = 80;
                if a x = 1;
                if bbb y = 2;
                if a
                    x = 1;
                if alpha_value > 0 && beta_value > 0 && gamma_value > 0 return;
                if left_value
                    && right_value return;
            }
            '''
        for contents in ['format_braceless_if = false\n',
                         'width = 40\nformat_braceless_if = false\n']:
            with self.subTest(contents=contents):
                self.write_config(contents)
                self.assert_format(source, disabled, '--width=40')
        self.write_config('width = 40\nformat_braceless_if = false\n')
        self.assert_format(source, disabled)

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
        bad_configs += [f'format_braceless_if = {value}\n' for value in ['0', '"false"', 'no', '[]']]
        bad_configs += ['width = 40\nformat_braceless_if = 1\n']
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
