# Running JavaScript

## eval

Evaluate a JavaScript expression and return the result.

```bash
patchright-cli eval --file=/tmp/expr.js
```

### Why use --file (not inline JS)

**Always use `--file` or stdin for `eval` and `run-code`.** Inline JS breaks because quotes get mangled through shell layers (bash -> uvx -> python).

```bash
# WRONG -- nested quotes break
patchright-cli eval "document.querySelector('a').href"

# RIGHT -- write to a temp file
cat > /tmp/check.js << 'JSEOF'
JSON.stringify({title: document.title, links: document.querySelectorAll('a').length})
JSEOF
patchright-cli eval --file=/tmp/check.js

# RIGHT -- pipe via stdin (simple expressions only)
echo 'document.title' | patchright-cli eval
```

### eval with element ref

Target a specific element by passing a ref as the second argument. The element is received as the first parameter of the expression:

```bash
echo 'el => el.textContent' | patchright-cli eval - e5
echo 'el => el.href' | patchright-cli eval - e3
```

### eval options

| Usage | Effect |
|-------|--------|
| `eval --file=<path>` | Read expression from file |
| `eval` (no args) | Read expression from stdin |
| `eval <expr> <ref>` | Evaluate on a specific element (element passed as first arg) |

## run-code

Run a JavaScript code block wrapped in `async () => { ... }`, so you can use `return` and `await`:

```bash
cat > /tmp/scroll.js << 'JSEOF'
window.scrollTo(0, document.body.scrollHeight);
return document.body.scrollHeight;
JSEOF
patchright-cli run-code --file=/tmp/scroll.js
```

### run-code options

| Usage | Effect |
|-------|--------|
| `run-code --file=<path>` | Read code from file |
| `run-code` (no args) | Read code from stdin |

## Temp file heredoc pattern

The recommended pattern for running JS from a shell:

```bash
cat > /tmp/myscript.js << 'JSEOF'
// Your JavaScript here
const data = document.querySelectorAll('.item');
return JSON.stringify([...data].map(el => el.textContent));
JSEOF
patchright-cli run-code --file=/tmp/myscript.js
```

Use `'JSEOF'` (quoted) to prevent shell variable expansion inside the heredoc.

## Common patterns

### Extract structured data

```bash
cat > /tmp/extract.js << 'JSEOF'
JSON.stringify(
  [...document.querySelectorAll('table tr')].map(row =>
    [...row.cells].map(cell => cell.textContent.trim())
  )
)
JSEOF
patchright-cli eval --file=/tmp/extract.js
```

### Wait for a custom condition

```bash
cat > /tmp/wait.js << 'JSEOF'
for (let i = 0; i < 30; i++) {
  if (document.querySelector('.results-loaded')) return true;
  await new Promise(r => setTimeout(r, 500));
}
return false;
JSEOF
patchright-cli run-code --file=/tmp/wait.js
```

### Scroll to bottom of infinite scroll

```bash
cat > /tmp/infinite.js << 'JSEOF'
let prev = 0;
for (let i = 0; i < 20; i++) {
  window.scrollTo(0, document.body.scrollHeight);
  await new Promise(r => setTimeout(r, 1000));
  if (document.body.scrollHeight === prev) break;
  prev = document.body.scrollHeight;
}
return document.body.scrollHeight;
JSEOF
patchright-cli run-code --file=/tmp/infinite.js
```

### Get element attribute or style

```bash
echo 'el => el.getAttribute("href")' | patchright-cli eval - e5
echo 'el => getComputedStyle(el).color' | patchright-cli eval - e3
```
