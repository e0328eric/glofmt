# glofmt

A Glosso formatter written in Glosso, using libglosso's C ABI and compiler
lexer. It collects operator declarations, matches delimiters, identifies
prefix/infix/postfix expressions and type constructors, and renders groups
with a width budget. Comments, literal spelling, raw-string contents, and BOM
are preserved. Formatting never executes input compile-time code.

The layout follows the [Rust style guide](https://doc.rust-lang.org/style-guide/expressions.html)
for operator spacing, calls, indexing, indentation, and width-driven argument
layout, and [zig fmt's renderer](https://github.com/ziglang/zig/blob/0.15.2/lib/std/zig/Ast/Render.zig)
for compact initializers and trailing-comma-driven vertical lists. Glosso's own
syntax, including quoted operators, `.{}`, `.[]`, `E!T`, and `?T`, takes priority.

Build with an installed Glosso compiler and its matching libglosso:

```sh
glosso compile first.glo --cache-dir=build/cache
build/glofmt source.glo              # format the file in place
build/glofmt --stdout source.glo     # preview without changing the file
build/glofmt --check source.glo
build/glofmt --tabs --indent=4 < source.glo
build/glofmt --width=80 source.glo
build/glofmt --stdin-filepath=src/example.glo < unsaved-buffer.glo
```

Install under `$HOME/.local/glofmt`:

```sh
glosso compile first.glo -- install
~/.local/glofmt/bin/glofmt source.glo
```

Use `glosso compile first.glo -- install --prefix /other/directory` to choose another
prefix. Add `$HOME/.local/glofmt/bin` to `PATH` to invoke `glofmt` directly.
The build script also accepts `build` (the default) and `help` after `--`.
Run it from the repository root.

On Linux, the build copies the compiler's matching shared library into
`share/libglosso.so`. Installation creates this layout:

```text
~/.local/glofmt/
  bin/glofmt
  share/libglosso.so
```

`src/glosso.glo` sets `#export "Glosso"(DYLIB_PREFIX = "$ORIGIN/../share");`.
The loader resolves `$ORIGIN` to the executable's directory, so the same
parameter works for both `build/glofmt` and the installed `bin/glofmt`.
The bundle can move and run from any working directory without a Glosso
installation. macOS uses `@executable_path/../share`; Windows puts `glosso.dll`
beside the executable. Only the shared library is needed at runtime because
the formatter tokenizes input without semantic checking or Bindgen.

For development with neighboring `glofmt/` and `glosso/` checkouts:

```sh
(cd ../glosso && ./build.exe libglosso)
../glosso/build/bin/glosso compile first.glo --cache-dir=build/cache
python3 tests/integration.py
python3 tests/installation.py --compiler ../glosso/build/bin/glosso
python3 tests/corpus.py --std "$HOME/.local/glosso/std"
```

With a filename, formatting atomically replaces that file and preserves its
permissions; successful formatting is silent. `--stdout` previews a file's
formatted contents without modifying it. With no filename, or `-`, input comes
from stdin and the formatted buffer goes to stdout. `--write`/`-w` remains an
explicit alias for the default file-editing behavior.
`--check` returns 1 if formatting would change the file, 0 if unchanged. I/O,
invalid UTF-8, lexical errors, and invalid arguments return 2. `--indent=N`
accepts 1 through 16 (default 4); `--tabs` selects tabs. `--write` requires a
filename and cannot be combined with `--check` or `--stdout`; `--check` and
`--stdout` also cannot be combined.

The preferred group width defaults to 100 columns. Set it persistently in
`get_config_dir()/glofmt/config.toml`, using Glosso std's
`File_System/Directory.get_config_dir()` to locate the configuration directory:

```toml
width = 80
```

On Linux and FreeBSD this is `$XDG_CONFIG_HOME/glofmt/config.toml`, falling back
to `$HOME/.config/glofmt/config.toml` if `XDG_CONFIG_HOME` is unset, empty, or
relative. On macOS it is `~/Library/Application Support/glofmt/config.toml`;
on Windows it is `glofmt/config.toml` under the Roaming AppData known folder.
The config file is optional; a missing file or omitted key keeps the default.
`format_braceless_if = false` turns off the braceless `if` layout described
below (default `true`). Other keys are ignored. Invalid TOML, an unreadable
config file, a `width` that is not an integer from 20 through 1000, or a
`format_braceless_if` that is not `true` or `false` returns 2 before formatting.

`--width=N` overrides the configured width and accepts the same range.
Configuration is still validated when the flag is supplied; `--help` and
`--version` do not load it. These settings apply to stdin, file formatting,
`--stdout`, and `--check`.

Long argument/initializer lists expand; an indivisible token or expression can
exceed the width. Existing trailing commas also expand lists, and are preserved
rather than inserted or removed. Empty blocks stay `{}`. Short initializers
use `Point.{ .x = 1, .y = 2 }`. Trailing comments stay with their statement;
up to one blank line between statements or comments is retained. Generated
line breaks use LF.

A chain with at least two method calls that does not fit on one line puts each
call (and any member access after the first call) on its own line, indented one
level. Leading field accesses stay with the root. A line break before any of
those elements inside a block also requests this layout:

```glosso
return environment_get_or(name, fallback, context.temp_allocator)
    .unwrap_or(fallback)
    .into_temporary();
```

As in the Rust style guide, the first call joins a root that ends left of
that indentation (`sb.write(a)` then `.write(b)` below it). As in rustfmt,
when the root ends with the closing delimiter of an expanded argument list,
the elements line up with that delimiter instead of indenting. A member access
alone on a continuation line is indented like other continuations.

Visibility groups (`#public { ... }` and `#private { ... }`, including public
audience modifiers) keep their contents at the surrounding indentation level,
with one blank line just inside each brace. Nested declarations still indent
normally, and empty groups stay `{}`. Inherent method blocks such as
`T ::: #public { ... }` indent their methods normally.

In `if x == { ... }` and `#if x == { ... }`, the `case` and `else;` arms align
with the `if` and its closing brace, and only the arm bodies are indented:

```glosso
if arg == {
case "-w", "--windowed";
    windowed = true;
else;
}
```

Nothing separates a braceless `if`'s condition from its body, so the layout
marks that boundary with a column or a line break. When every arm of an
`if`/`else if`/`else` chain is a single statement and the aligned lines fit
the width, conditions and bodies line up:

```glosso
if      options.width > 0         r.width = options.width;
else if r.tty && caps.columns > 0 r.width = caps.columns;
else                              r.width = 80;
```

Otherwise every body in the chain moves to its own line, indented one level.
A line break inside any arm also requests this layout. A lone `if` keeps its
body on the same line while it fits:

```glosso
if options.width > 0
    r.width = options.width;
else if #pattern ?n = env_columns_or_terminal_fallback(default_columns)
    r.width = n;
else
    r.width = 80;
```

Consecutive lone `if` statements line up their bodies the same way while the
aligned lines fit. A blank line or comment ends the group, as does an `if`
that takes more than one line or would push the aligned lines past the width;
the latter starts the next group:

```glosso
if prev.prefix && (fmt_symbolic(prev) || prev.text == "...")            space = false;
if prev.text == ".." || prev.text == "..="                              space = false;
if prev.text == "]" && prev.pair >= 0 && tokens.*[prev.pair].type_array space = false;
```

A condition that still overflows breaks after its top-level `||` operators,
or after `&&` when it has no top-level `||`, filling each line as far as the
width allows. Continuation lines line up under the condition's first token
(after `if ` or `else if `; with `--tabs`, tabs indent and spaces align), and
the body stays one level deep. A line break before one of these operators
moves after it:

```glosso
if prev_text == "(" || prev_text == "[" || prev_text == ".[" || prev_text == "." ||
   prev_text == "$" || prev_text == "`" || prev_text == "``"
    space = false;
```

Chains with a braced arm, a comment between arms, or a nested control-flow
body keep the ordinary layout. With `format_braceless_if = false` in the
config, every braceless `if` keeps it: tokens are spaced as usual, but bodies
and conditions keep their existing line breaks and are never aligned or broken.

Use `// glofmt: off` and `// glofmt: on` to preserve manual formatting:

```glosso
// glofmt: off
matrix := .[1,  0,  0,
            0,  1,  0,
            0,  0,  1];
// glofmt: on
```

The source after the `off` comment through the next `on` comment is copied
byte-for-byte, including whitespace, line endings, and operator spelling.
Formatting resumes after `on`; without it, the rest of the file is preserved,
including any missing final newline. Directives do not nest: repeated `off`
comments stay disabled, and an `on` while already enabled has no effect.
Only line comments whose trimmed contents are exactly `glofmt: off` or
`glofmt: on` are recognized. They may follow code on the same line; strings,
block comments, and doc comments do not toggle formatting.

Disabled regions still participate in delimiter matching and operator/import
discovery, so formatting resumes with the surrounding syntax intact. They
must still lex successfully; existing invalid-input handling applies.

Unary operators attach to their operand, and calls, indexing, and field access
attach to their base:

```glosso
// Before
main::(){a:=-1;b:=x*-2;c:=foo.bar[0];}

// After, when no '*-' operator is declared
main :: () {
    a := -1;
    b := x * -2;
    c := foo.bar[0];
}
```

Glosso lexes `*-` as one operator. If `'*-'` is declared, that expression
instead becomes `x *- 2`. The formatter scans declarations before uses,
including literal source imports and named standard-library imports and
exports. The Prelude operator seed matches `std/Prelude/Primitive_Operators.glo`
and `Primitive_Typeclasses.glo`, so standalone bundles can still format without
an SDK. `--std=DIR` selects the std directory for named imports; the default is
the SDK used to build the formatter. Source imports resolve relative to the
input file, or to `--stdin-filepath=PATH` for an unsaved stdin buffer. That
option supplies context only and does not read or write PATH.

Unknown operator runs of at most 16 ASCII bytes are split only into known
operators followed by known prefix operators, and only when all imports can
be resolved statically. An unresolved import, escaped/computed import path,
`#insert`, or `#load_module` prevents this repair. Collected declarations are
conservative: conditional/private declarations also reserve their spelling;
the formatter does not perform type or visibility checking. Named modules
outside the selected std directory remain unresolved.

Operator repair is the only intentional token change. The result is lexed
again and every significant token is compared against the repaired input.
Separate prefix operators remain separate (`! !x` cannot become `!!x`).
Unbalanced or mismatched delimiters and unsafe layouts return the original
source. Lexical errors are reported by the CLI without overwriting the file.

`src/format.glo` contains expression spacing and group layout;
`src/syntax.glo` holds lossless token/delimiter facts; `src/operators.glo`
collects declarations and resolves glued operators. `main.glo` implements the
CLI and atomic writes; `src/config.glo` loads the user configuration.
`src/glosso.glo` loads the shared library. The Python
tests use the neighboring compiler checkout's C ABI harness with the bundled
library to independently compare token streams. `tests/corpus.py` checks an
installed std without modifying it, including idempotence for every file.
