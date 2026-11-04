// Guards against literal escape sequences in rendered JSX text.
//
// JavaScript string literals process escapes ("Loading\u2026" renders "Loading…"),
// but JSX text nodes and JSX attribute string literals do NOT:
//   <p>Loading\u2026</p>             renders the six characters "\u2026"
//   <input placeholder="Ask\u2026" /> same problem
// This check parses every .ts/.tsx file under src/ with the TypeScript compiler
// and reports \uXXXX, \u{...} and \xXX only in those two JSX positions. Escapes in
// ordinary JS strings, template literals, regexes and comments are valid and ignored.
//
// Usage (from frontend/):  node scripts/check-jsx-escapes.mjs
// Add --all to also list the valid (ignored) occurrences, for audits.
import fs from "node:fs"
import path from "node:path"
import ts from "typescript"

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/, "$1")),
  "..",
  "src",
)
const ESCAPE = /\\(?:u[0-9A-Fa-f]{4}|u\{[0-9A-Fa-f]+\}|x[0-9A-Fa-f]{2})/g
const listAll = process.argv.includes("--all")

function* walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else if (/\.(ts|tsx)$/.test(e.name) && !e.name.endsWith(".d.ts")) yield p
  }
}

const bad = []
const ok = []
for (const file of walk(ROOT)) {
  const text = fs.readFileSync(file, "utf8")
  const sf = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )
  const rel = path.relative(path.dirname(ROOT), file).replace(/\\/g, "/")
  const visit = (node) => {
    const isJsxText = node.kind === ts.SyntaxKind.JsxText
    const isJsxAttrString =
      node.kind === ts.SyntaxKind.StringLiteral &&
      node.parent?.kind === ts.SyntaxKind.JsxAttribute
    const isLiteral =
      ts.isStringLiteralLike(node) ||
      ts.isTemplateLiteralToken(node) ||
      node.kind === ts.SyntaxKind.RegularExpressionLiteral
    if (isJsxText || isLiteral) {
      const raw = node.getText(sf)
      for (const m of raw.matchAll(ESCAPE)) {
        const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf) + m.index)
        const where = `${rel}:${line + 1}  ${m[0]}`
        if (isJsxText) bad.push(`${where}  (JSX text)`)
        else if (isJsxAttrString) bad.push(`${where}  (JSX attribute string)`)
        else ok.push(`${where}  (${ts.SyntaxKind[node.kind]})`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
}

if (listAll) {
  console.log(`Valid escapes (JS strings/templates/regex): ${ok.length}`)
  for (const l of ok) console.log("  ok   " + l)
}
if (bad.length) {
  console.error(
    `Literal escape sequences that would render as visible text: ${bad.length}`,
  )
  for (const l of bad) console.error("  BAD  " + l)
  console.error('Use the real character (e.g. "…", "—"), or wrap it as {"\\u2026"}.')
  process.exit(1)
}
console.log("OK: no literal escape sequences in JSX text or JSX attribute strings.")
