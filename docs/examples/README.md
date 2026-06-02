# Custom label-schema templates

A **custom schema** is the label taxonomy that TEE's schema browser loads
(features.md §5, "Custom — user-supplied JSON or tab-indented text"). It
defines the codes, names and hierarchy you can pick from when labelling; it is
*not* a label export file (that format, with embeddings and pixel coordinates,
is described in [`../label-format.md`](../label-format.md) and is produced by
the app, not hand-authored).

Load a file via the schema browser's **Custom** option. The loader
(`public/js/schema.js`) accepts three shapes:

The templates are bundled with the app under `public/schemas/`:

| File | Shape |
|------|-------|
| [`custom-schema-template.json`](../../public/schemas/custom-schema-template.json) | Object with a `tree` array (recommended) |
| — | A bare top-level array `[ <node>, ... ]` (the `name`/`version`/`date` wrapper is then omitted; the filename becomes the schema name) |
| [`custom-schema-template.txt`](../../public/schemas/custom-schema-template.txt) | Tab-indented text fallback |

## JSON format

```json
{
  "name": "My Custom Schema",        // optional; shown in the browser
  "version": "1.0",                  // optional, informational
  "date": "2026-06-02",              // optional, informational
  "tree": [                          // required: array of nodes
    { "code": "a", "name": "Class A", "children": [ ... ] }
  ]
}
```

Each **node** has:

- `code` — short identifier (e.g. `g1a`). Used for colour assignment and shown
  in brackets on the selected label. May be empty (`""`) for grouping-only rows.
- `name` — human-readable label name.
- `children` — array of child nodes; `[]` for a leaf.

Nodes can nest to any depth. Selecting any node — leaf or branch — sets the
active label's code, name and an auto-assigned colour (hashed from the code).

## Tab-indented text format

A plain-text fallback for quick authoring. Indent **4 spaces per level** (the
parser divides leading-whitespace columns by 4 — literal tab characters count
as a single column and will not nest, despite the format's name). Each line is
`<code> <name>`; a line with no leading code token is treated as a name-only
node with an empty code.

```
a Top-level class A
    a1 Sub-class A1
        a1a Leaf A1a
b Top-level class B
```

Compare the built-in schemas in [`../../public/schemas/`](../../public/schemas/)
(`ukhab-v2.json`, `eunis.json`, `hotw.json`) for full real-world examples.
