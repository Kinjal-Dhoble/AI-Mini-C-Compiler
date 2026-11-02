"use strict";
/* MiniC Studio front-end. Talks to ide/server.py; the editor is Monaco (bundled locally). */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("minic." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("minic." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data;
  try { data = await r.json(); } catch { throw new Error(`Server returned ${r.status}`); }
  if (!r.ok) throw new Error(data.error || `Server returned ${r.status}`);
  return data;
}

/* POST that streams newline-delimited JSON events (used for AI requests): onEvent(e) for each line. */
async function apiStream(path, body, onEvent) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, stream: true }) });
  if (!r.ok || !r.body) throw new Error(`Server returned ${r.status}`);
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "", result = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      const e = JSON.parse(line);
      if (e.type === "error") throw new Error(e.error);
      if (e.type === "result") result = e.report; else onEvent(e);
    }
  }
  if (!result) throw new Error("The AI request ended without an answer.");
  return result;
}

const S = {
  info: null, files: [], tabs: [], active: null,
  compile: null, compileFor: null, compileSeq: 0,
  build: null,                // {for: version key, result, run}
  ai: { ok: false, model: null, models: [], busy: 0, pipeline: false },
  debug: null, breakpoints: store.get("breakpoints", {}),
  targetMode: "c", cfgDirty: true, traceDirty: true,
  frontend: store.get("frontend", "hand"),
};
let monaco, editor, diffEditor;
const deco = {};

/* ------------------------------------------------------------------ boot */
window.MonacoEnvironment = {
  getWorkerUrl() {
    const base = new URL("vendor/monaco/", location.href).href;
    return URL.createObjectURL(new Blob([`self.MonacoEnvironment={baseUrl:${JSON.stringify(base)}};importScripts(${JSON.stringify(base + "vs/base/worker/workerMain.js")});`], { type: "text/javascript" }));
  },
};
require.config({ paths: { vs: "vendor/monaco/vs" } });
require(["vs/editor/editor.main"], () => { monaco = window.monaco; setupMonaco(); init().catch((e) => toast(e.message)); },
  () => { document.body.innerHTML = '<p style="padding:24px">The editor could not load. Check that the ide/static/vendor folder was extracted completely.</p>'; });

function setupMonaco() {
  monaco.languages.register({ id: "minic", extensions: [".c"] });
  monaco.languages.setMonarchTokensProvider("minic", {
    keywords: ["int", "void", "if", "else", "while", "for", "return", "char", "short", "long", "float", "double", "sizeof"],
    tokenizer: {
      root: [
        [/\/\/.*$/, "comment"], [/\/\*/, "comment", "@comment"],
        [/^\s*#\s*include.*$/, "meta"],
        [/"/, "string", "@string"],
        [/\b(printf|scanf)\b/, "keyword.print"],
        [/\bprint\b/, "keyword.print"],
        [/\b(strlen|strcpy|strncpy|strcat|strncat|strcmp|strncmp|toupper|tolower|isupper|islower|isalpha|isdigit|isalnum|isspace|abs|sqrt|pow|fabs|floor|ceil|puts|putchar)\b/, "keyword.print"],
        [/'(?:[^'\\]|\\.)*'/, "string"],
        [/[A-Za-z_]\w*/, { cases: { "@keywords": "keyword", "@default": "identifier" } }],
        [/(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?[fF]?|\d+[eE][+-]?\d+[fF]?/, "number"],
        [/\d+/, "number"], [/[{}()\[\]]/, "@brackets"], [/[;,]/, "delimiter"],
        [/==|!=|<=|>=|&&|\|\||[+\-*/%<>=!]/, "operator"],
      ],
      comment: [[/[^/*]+/, "comment"], [/\*\//, "comment", "@pop"], [/[/*]/, "comment"]],
      string: [[/%[-+ 0#]*\d*(?:\.\d+)?(?:hh|h|ll|l)?[diuxXocsfFeEgG%]/, "string.format"], [/\\[nt0r'"\\]/, "string.escape"], [/[^"%\\]+/, "string"], [/[%\\]/, "string"], [/"/, "string", "@pop"]],
    },
  });
  monaco.languages.setLanguageConfiguration("minic", {
    comments: { lineComment: "//", blockComment: ["/*", "*/"] },
    brackets: [["{", "}"], ["(", ")"], ["[", "]"]],
    autoClosingPairs: [{ open: "{", close: "}" }, { open: "(", close: ")" }, { open: "[", close: "]" }, { open: "/*", close: " */" }, { open: '"', close: '"', notIn: ["string", "comment"] }, { open: "'", close: "'", notIn: ["string", "comment"] }],
    indentationRules: { increaseIndentPattern: /\{[^}]*$/, decreaseIndentPattern: /^\s*\}/ },
  });
  const snip = (label, text, doc) => ({ label, kind: monaco.languages.CompletionItemKind.Snippet, insertText: text, insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, documentation: doc });
  monaco.languages.registerCompletionItemProvider("minic", {
    provideCompletionItems(model, pos) {
      const w = model.getWordUntilPosition(pos);
      const range = { startLineNumber: pos.lineNumber, endLineNumber: pos.lineNumber, startColumn: w.startColumn, endColumn: w.endColumn };
      const items = [
        snip("main", "int main() {\n\t$0\n\treturn 0;\n}", "Program entry point"),
        snip("function", "int ${1:name}(int ${2:x}) {\n\t$0\n\treturn ${2:x};\n}", "Define a function"),
        snip("printf", "printf(\"${1:%d}\\n\", ${2:x});", "Print text and values: %d int, %ld long, %f float/double, %c char, %s string"),
        snip("array", "int ${1:marks}[${2:5}] = {${3:0}};", "An array: a numbered list of values (indexes 0 to size - 1)"),
        snip("foreach", "for (${1:i} = 0; $1 < ${2:5}; $1 = $1 + 1) {\n\t${3:marks}[$1]$0\n}", "Loop over every element of an array: use <, not <="),
        snip("string", "char ${1:name}[${2:20}] = \"${3:text}\";", "A string is a char array; leave room for the '\\0' at the end"),
        snip("strcpy", "strcpy(${1:dest}, ${2:src});", "Copy a string (dest must be big enough, + 1 for '\\0')"),
        snip("strcmp", "strcmp(${1:a}, ${2:b}) == 0", "Are two strings equal? (== compares addresses, not text)"),
        snip("strlen", "strlen(${1:s})", "Number of characters before the '\\0'"),
        snip("scanfs", "scanf(\"%s\", ${1:name});", "Read one word into a char array (no &)"),
        snip("print", "print(${1:x});", "Short form: one integer and a new line"),
        snip("scanf", "scanf(\"%d\", &${1:x});", "Read an int from the Input box into a variable"),
        snip("include", "#include <stdio.h>", "Optional: makes the program valid standard C"),
        snip("if", "if (${1:x > 0}) {\n\t$0\n}", "if statement"),
        snip("ifelse", "if (${1:x > 0}) {\n\t$2\n} else {\n\t$0\n}", "if / else"),
        snip("while", "while (${1:i < 10}) {\n\t$0\n}", "while loop"),
        snip("for", "for (${1:i} = 0; $1 < ${2:10}; $1 = $1 + 1) {\n\t$0\n}", "for loop (use i = i + 1, not i++)"),
        ...["int", "void", "return", "else", "char", "short", "long", "float", "double", "sizeof"].map((k) => ({ label: k, kind: monaco.languages.CompletionItemKind.Keyword, insertText: k })),
        ...["strcat", "strncpy", "strncat", "strncmp", "toupper", "tolower", "isupper", "islower", "isalpha", "isdigit", "isalnum", "isspace", "abs", "sqrt", "pow", "fabs", "floor", "ceil", "puts", "putchar"]
          .map((k) => ({ label: k, kind: monaco.languages.CompletionItemKind.Function, insertText: k + "($0)", insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, detail: "built-in library function" })),
        ...((S.compile?.ast?.statements || []).filter((f) => f.type === "Function").map((f) => ({
          label: f.name, kind: monaco.languages.CompletionItemKind.Function, detail: `${f.return_type} ${f.name}(${f.params.map((p) => p[0] + " " + p[1] + (p[4] ? "[]" : "")).join(", ")})`,
          insertText: `${f.name}(${f.params.map((p, i) => "${" + (i + 1) + ":" + p[1] + "}").join(", ")})`, insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet }))),
        ...[...new Set((S.compile?.tokens || []).filter((t) => t.kind === "IDENTIFIER" && !(S.compile?.functions || []).includes(t.value)).map((t) => t.value))]
          .map((v) => ({ label: v, kind: monaco.languages.CompletionItemKind.Variable, insertText: v })),
      ];
      return { suggestions: items.map((i) => ({ ...i, range })) };
    },
  });
  monaco.editor.defineTheme("minic", {
    base: "vs-dark", inherit: true,
    rules: [
      { token: "keyword", foreground: "C9B6FF" }, { token: "keyword.print", foreground: "A98BFF", fontStyle: "bold" },
      { token: "number", foreground: "FFB38A" }, { token: "comment", foreground: "7E7495" },
      { token: "string", foreground: "F0D49A" }, { token: "string.format", foreground: "A98BFF", fontStyle: "bold" },
      { token: "string.escape", foreground: "8CC8F0" }, { token: "meta", foreground: "9D94B2" },
      { token: "operator", foreground: "E2DCE8" }, { token: "identifier", foreground: "EEE8F0" }, { token: "delimiter", foreground: "ADA4C0" },
    ],
    colors: {
      "editor.background": "#1B1626", "editor.foreground": "#EEE8F0", "editorLineNumber.foreground": "#5B5173",
      "editorLineNumber.activeForeground": "#A98BFF", "editor.lineHighlightBackground": "#A98BFF0A", "editor.lineHighlightBorder": "#00000000",
      "editor.selectionBackground": "#A98BFF38", "editor.inactiveSelectionBackground": "#A98BFF1F", "editorCursor.foreground": "#A98BFF",
      "editor.wordHighlightBackground": "#A98BFF22", "editor.findMatchBackground": "#A98BFF60", "editor.findMatchHighlightBackground": "#A98BFF30",
      "editorBracketMatch.background": "#A98BFF2A", "editorBracketMatch.border": "#A98BFF",
      "editorBracketHighlight.foreground1": "#EEE8F0", "editorBracketHighlight.foreground2": "#C9B6FF", "editorBracketHighlight.foreground3": "#F0D49A",
      "editorIndentGuide.background1": "#2A2339", "editorIndentGuide.activeBackground1": "#53476F",
      "editorGutter.background": "#1B1626", "minimap.background": "#1B1626", "minimapSlider.background": "#A98BFF1F",
      "scrollbarSlider.background": "#3F365680", "scrollbarSlider.hoverBackground": "#A98BFF40",
      "editorError.foreground": "#FF5A65", "editorWarning.foreground": "#F0B44C", "editorInfo.foreground": "#8CC8F0",
      "diffEditor.insertedTextBackground": "#A98BFF2A", "diffEditor.removedTextBackground": "#FF5A652A",
      "diffEditor.insertedLineBackground": "#A98BFF12", "diffEditor.removedLineBackground": "#FF5A6512",
      "editorWidget.background": "#16121F", "editorHoverWidget.background": "#16121F", "editorHoverWidget.border": "#3F3656",
      "editorSuggestWidget.background": "#16121F", "editorSuggestWidget.selectedBackground": "#A98BFF2E", "editorSuggestWidget.highlightForeground": "#A98BFF",
      "focusBorder": "#A98BFF",
    },
  });
  editor = monaco.editor.create($("#editor"), {
    model: null, theme: "minic", language: "minic", automaticLayout: true, glyphMargin: true,
    fontFamily: "Overpass Mono, Consolas, monospace", fontSize: 14, lineHeight: 23, tabSize: 4, insertSpaces: true,
    minimap: { enabled: true, renderCharacters: false }, scrollBeyondLastLine: false, fixedOverflowWidgets: true,
    renderLineHighlight: "all", smoothScrolling: true, padding: { top: 8 }, bracketPairColorization: { enabled: true },
  });
  for (const k of ["exec", "bp", "irsrc"]) deco[k] = editor.createDecorationsCollection();
  editor.onDidChangeModelContent(() => { markDirty(); scheduleCompile(); });
  editor.onDidChangeCursorPosition((e) => { $("#sbCursor").textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`; highlightIrForLine(e.position.lineNumber); });
  editor.onMouseDown((e) => {
    if (e.target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN || e.target.type === monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS && e.event.altKey) {
      toggleBreakpoint(e.target.position.lineNumber);
    }
  });
}

async function init() {
  wireUi();
  S.info = await api("/api/info");
  renderToolchain();
  await refreshFiles();
  const open = store.get("openTabs", []).filter((n) => S.files.some((f) => f.name === n));
  const first = open.length ? open : [S.files.find((f) => f.name === "hello.c")?.name || S.files[0]?.name].filter(Boolean);
  for (const n of first) await openFile(n, false);
  const last = store.get("activeTab", null);
  if (last && S.tabs.some((t) => t.name === last)) activate(last);
  else if (S.tabs.length) activate(S.tabs[0].name);
  checkAi();
  setInterval(() => { if (!S.ai.ok) checkAi(true); }, 20000);
  const h = store.get("panelHeight", null); if (h) $("#panel").style.height = h + "px";
  if (store.get("aiHidden", false)) toggleAiPanel(false);
  $("#aiAuto").checked = store.get("aiAuto", true);
  $("#optO2").checked = store.get("optO2", false);
}

/* ------------------------------------------------------------- files/tabs */
async function refreshFiles() {
  S.files = await api("/api/files");
  renderFiles();
}
function renderFiles() {
  $("#fileList").innerHTML = S.files.map((f) => {
    const tab = S.tabs.find((t) => t.name === f.name);
    return `<li data-name="${esc(f.name)}" class="${f.name === S.active ? "active" : ""}" title="${esc(f.name)}">${esc(f.name)}${tab && isDirty(tab) ? '<span class="dirty" aria-label="unsaved"></span>' : ""}</li>`;
  }).join("") || '<li class="muted" style="cursor:default">No .c files yet</li>';
}
async function openFile(name, focus = true) {
  let tab = S.tabs.find((t) => t.name === name);
  if (!tab) {
    const f = await api(`/api/file?name=${encodeURIComponent(name)}`);
    const model = monaco.editor.createModel(f.content, "minic", monaco.Uri.parse("inmemory://workspace/" + name));
    tab = { name, model, saved: f.content, view: null };
    S.tabs.push(tab);
  }
  if (focus) activate(name);
  persistTabs();
  return tab;
}
function tabOf(name = S.active) { return S.tabs.find((t) => t.name === name); }
function isDirty(t) { return t.model.getValue() !== t.saved; }
function activate(name) {
  if (S.debug) stopDebug();
  const cur = tabOf();
  if (cur) cur.view = editor.saveViewState();
  S.active = name;
  const t = tabOf(name);
  editor.setModel(t.model);
  if (t.view) editor.restoreViewState(t.view);
  editor.focus();
  S.compile = null; S.build = null; S.cfgDirty = true;
  renderTabs(); renderFiles(); renderBreakpoints();
  $("#sbFile").textContent = name;
  store.set("activeTab", name);
  $("#stdinBox").value = store.get("stdin:" + name, "");
  compileNow({ quiet: true });
}
function renderTabs() {
  $("#tabs").innerHTML = S.tabs.map((t) => `<div class="tab ${t.name === S.active ? "active" : ""} ${isDirty(t) ? "dirty" : ""}" data-name="${esc(t.name)}" role="tab" aria-selected="${t.name === S.active}">
    ${esc(t.name)}<button class="x" data-close="${esc(t.name)}" title="Close"><span>×</span></button></div>`).join("");
}
function markDirty() { renderTabs(); renderFiles(); }
function closeTab(name) {
  const t = tabOf(name); if (!t) return;
  if (isDirty(t) && !confirm(`${name} has unsaved changes. Close it anyway?`)) return;
  S.tabs = S.tabs.filter((x) => x !== t);
  t.model.dispose();
  if (S.active === name) { S.active = null; if (S.tabs.length) activate(S.tabs[S.tabs.length - 1].name); else { editor.setModel(null); renderTabs(); } }
  else renderTabs();
  persistTabs(); renderFiles();
}
function persistTabs() { store.set("openTabs", S.tabs.map((t) => t.name)); }
async function saveActive(backup = false) {
  const t = tabOf(); if (!t) return;
  const content = t.model.getValue();
  const r = await api("/api/file", { name: t.name, content, backup });
  t.saved = content;
  markDirty(); await refreshFiles();
  return r;
}
async function newFile(name, content) {
  if (!/\.c$/.test(name)) name += ".c";
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]*\.c$/.test(name)) { toast("Use letters, digits, _ . - and end the name with .c"); return null; }
  if (S.files.some((f) => f.name === name)) { await openFile(name); return name; }
  await api("/api/file", { name, content });
  await refreshFiles();
  await openFile(name);
  return name;
}
function freeName(stem, suffix) {
  let n = `${stem}.${suffix}.c`, i = 2;
  while (S.files.some((f) => f.name === n) || S.tabs.some((t) => t.name === n)) n = `${stem}.${suffix}${i++}.c`;
  return n;
}

/* --------------------------------------------------------------- compile */
let compileTimer;
function scheduleCompile() { clearTimeout(compileTimer); compileTimer = setTimeout(() => compileNow({ quiet: true }), 450); }
function versionKey() { const t = tabOf(); return t ? `${t.name}#${t.model.getVersionId()}` : null; }

async function compileNow({ quiet = false } = {}) {
  const t = tabOf(); if (!t) return null;
  const seq = ++S.compileSeq, key = versionKey(), source = t.model.getValue();
  if (!quiet) setState("busy", "Compiling…");
  let r;
  try { r = await api("/api/compile", { source, name: t.name, frontend: S.frontend }); }
  catch (e) { setState("fail", "Server unavailable"); toast(e.message); return null; }
  if (seq !== S.compileSeq || key !== versionKey()) return null;     // stale
  applyCompile(r, key);
  return r;
}

function applyCompile(r, key = versionKey()) {
  S.compile = r; S.compileFor = key; S.cfgDirty = true;
  if (S.build && S.build.for !== key) S.build = null;
  const t = tabOf(); if (!t) return;
  monaco.editor.setModelMarkers(t.model, "minic", r.diagnostics.map((d) => {
    const word = t.model.getWordAtPosition({ lineNumber: d.line, column: d.column });
    const maxCol = t.model.getLineMaxColumn(Math.min(d.line, t.model.getLineCount()));
    return {
      severity: { error: 8, warning: 4, info: 2 }[d.severity], message: `${d.message}\n\n${d.explanation}${d.suggestion ? "\n\nFix: " + d.suggestion : ""}`,
      startLineNumber: d.line, startColumn: d.column, endLineNumber: d.line,
      endColumn: word ? word.endColumn : Math.min(d.column + 1, maxCol), source: d.category,
    };
  }));
  renderRail(); renderProblems(); renderTokens(); renderAst(); renderIr(); renderOutline(); renderTarget(); updateAiButtons();
  if ($("#view-cfg").classList.contains("active")) renderCfg();
  S.callsDirty = true;
  if ($("#view-calls").classList.contains("active")) renderCalls();
  S.traceDirty = true;
  if ($("#view-trace").classList.contains("active")) renderTrace();
  $("#stdinBar").classList.toggle("needed", !!r.reads_input);
  $("#stdinHint").textContent = r.reads_input ? "this program uses scanf" : "";
  const errs = r.diagnostics.filter((d) => d.severity === "error").length;
  const warns = r.diagnostics.filter((d) => d.severity === "warning").length;
  $("#sbProblems").textContent = `${errs} error${errs === 1 ? "" : "s"}, ${warns} warning${warns === 1 ? "" : "s"}`;
  if (!S.debug) setState(r.success ? "ok" : "fail", r.success ? "Compiled" : stageFailText(r));
}
function stageFailText(r) { return { lex: "Lexical error", parse: "Syntax error", semantic: "Semantic error" }[r.stage] || "Compile failed"; }

/* ------------------------------------------------------------------ rail */
function setStage(stage, state, metric = "") {
  const li = $(`#rail li[data-stage="${stage}"]`);
  li.className = state || "";
  $("small", li).textContent = metric;
}
function countAst(n) {
  if (Array.isArray(n)) return n.reduce((a, x) => a + countAst(x), 0);
  if (n && typeof n === "object" && n.type) return 1 + Object.values(n).reduce((a, x) => a + (typeof x === "object" ? countAst(x) : 0), 0);
  return 0;
}
function renderRail() {
  const r = S.compile; if (!r) return;
  const errs = r.diagnostics.filter((d) => d.severity === "error").length;
  const warns = r.diagnostics.filter((d) => d.severity === "warning").length;
  const order = ["lex", "parse", "semantic", "ir"];
  const failAt = { lex: 0, parse: 1, semantic: 2 }[r.stage];
  order.forEach((s, i) => {
    if (r.success || i < failAt) {
      const metric = { lex: `${r.tokens.length} tokens`, parse: `${r.frontend === "yacc" ? "LALR, " : ""}${countAst(r.ast)} nodes`, semantic: warns ? `${warns} warning${warns > 1 ? "s" : ""}` : "no issues", ir: `${r.ir.length} instr` }[s];
      setStage(s, "pass" + (s === "semantic" && warns ? " warn" : ""), metric);
    } else if (i === failAt) setStage(s, "fail", `${errs || 1} error${errs > 1 ? "s" : ""}`);
    else setStage(s, "skip", "");
  });
  const b = S.build;
  if (!r.success) { setStage("build", "skip"); setStage("run", "skip"); }
  else if (!b) { setStage("build", "", ""); setStage("run", "", ""); }
  else {
    setStage("build", b.result.ok ? "pass" : "fail", b.result.ok ? (b.result.mode === "native" ? toolShort() : "VM") : "failed");
    if (b.run) setStage("run", b.run.timed_out || b.run.stderr ? "fail" : "pass", b.run.timed_out ? "timed out" : `exit ${b.run.exit_code}`);
    else setStage("run", "", "");
  }
}
function toolShort() { const tc = S.info?.toolchain; return tc?.mode === "native" ? `${tc.family} ${(tc.version.match(/\d+\.\d+/) || [""])[0]}`.trim() : "VM"; }
function renderToolchain() {
  const tc = S.info.toolchain, chip = $("#toolchainChip");
  if (tc.mode === "native") { chip.textContent = `Native executables, ${toolShort()}`; chip.title = `${tc.version}\n${tc.compiler}`; }
  else { chip.textContent = "Portable VM executables"; chip.title = "No gcc, clang or tcc was found, so programs are built for the MiniC virtual machine.\nInstall MinGW-w64 (for example through MSYS2) and add its bin folder to PATH to get native .exe files."; }
}

/* ------------------------------------------------------------ panel views */
function openGuide() {
  const p = $("#panel"), want = Math.round(window.innerHeight * 0.55);
  if (p.offsetHeight < want) p.style.height = want + "px";
  showView("guide");
}
function showView(v) {
  $$(".panel-tabs > button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
  if (v === "cfg" && S.cfgDirty) renderCfg();
  if (v === "calls" && S.callsDirty !== false) renderCalls();
  if (v === "trace" && S.traceDirty) renderTrace();
}
function gotoLine(line, col = 1, focus = true) {
  if (!line) return;
  editor.revealLineInCenterIfOutsideViewport(line);
  editor.setPosition({ lineNumber: line, column: col });
  if (focus) editor.focus();
}

function renderProblems() {
  const d = S.compile?.diagnostics || [];
  const errs = d.filter((x) => x.severity === "error").length;
  const c = $("#problemCount"); c.textContent = d.length || ""; c.className = "count" + (errs ? " err" : "");
  $("#problems").innerHTML = d.length ? d.map((x, i) => `<li class="${x.severity}" data-i="${i}"><span class="sev"></span>
    <span class="msg">${esc(x.category)}: ${esc(x.message)}</span><span class="loc">Ln ${x.line}, Col ${x.column}</span>
    <span class="expl">${esc(x.explanation)}${x.suggestion ? " Fix: " + esc(x.suggestion) : ""}${x.recovery ? `<br><span class="muted">Parser recovery: ${esc(x.recovery)}</span>` : ""}</span></li>`).join("")
    : '<li class="empty-view">No problems found.</li>';
}
function renderTokens() {
  const t = S.compile?.tokens || [];
  $("#tokens").innerHTML = t.length ? t.map((k, i) => `<span class="tok ${k.kind}" data-i="${i}" title="${k.kind} at line ${k.line}, column ${k.column}"><span>${esc(k.value)}</span><small>${k.kind.toLowerCase()} ${k.line}:${k.column}</small></span>`).join("")
    : '<div class="empty-view">The lexer stopped before producing tokens. See Problems.</div>';
}
function astLabel(n) {
  const bits = [];
  for (const k of ["name", "op", "value", "var_type", "return_type", "to_type", "of_type"]) if (n[k] !== undefined && n[k] !== "" && n[k] !== null && typeof n[k] !== "object") bits.push(["var_type", "return_type", "to_type", "of_type"].includes(k) ? n[k] : k === "op" ? `'${n[k]}'` : k === "value" && n.type === "String" ? `"${n[k]}"` : n[k]);
  if (Array.isArray(n.dims) && n.dims.length) bits.push(n.dims.map((d) => d && d.value !== undefined ? `[${d.value}]` : "[]").join(""));
  return bits.join(" ");
}
function astHtml(n, key, depth) {
  if (Array.isArray(n)) {
    if (!n.length) return "";
    if (n.every((x) => x && typeof x === "object" && x.type)) return n.map((x) => astHtml(x, key, depth)).join("");
    return `<div class="leaf"><span class="key">${esc(key)}:</span> ${esc(JSON.stringify(n))}</div>`;
  }
  if (!n || typeof n !== "object") return "";
  const kids = Object.entries(n).filter(([k, v]) => !["type", "line", "column"].includes(k) && v && typeof v === "object").map(([k, v]) => astHtml(v, k, depth + 1)).join("");
  const keyTxt = key && !["statements"].includes(key) ? `<span class="key">${esc(key)} </span>` : "";
  const head = `${keyTxt}<span class="ty">${esc(n.type)}</span> <span class="lbl">${esc(astLabel(n))}</span><span class="ln" data-line="${n.line}">line ${n.line}</span>`;
  return kids ? `<details ${depth < 4 ? "open" : ""}><summary>${head}</summary>${kids}</details>` : `<div class="leaf">${head}</div>`;
}
function renderAst() {
  const a = S.compile?.ast;
  $("#ast").innerHTML = a ? astHtml(a, "", 0) : '<div class="empty-view">No syntax tree: parsing did not finish. See Problems.</div>';
}
function irClass(ins) {
  if (/^(LABEL|FUNC|END_FUNC)/.test(ins)) return "op-label";
  if (/^(GOTO|IF_FALSE|RETURN)/.test(ins)) return "op-jump";
  if (/^(PRINT|WRITE_|READ)/.test(ins)) return "op-print";
  return "";
}
function renderIr() {
  const r = S.compile;
  $("#ir").innerHTML = r?.ir?.length ? r.ir.map((ins, i) => `<li data-i="${i}" data-line="${r.line_map[i]}"><span class="idx">${i}</span><span class="${irClass(ins)}">${esc(ins)}</span><span class="src">line ${r.line_map[i]}</span></li>`).join("")
    : '<div class="empty-view">Intermediate code appears once the program passes semantic analysis.</div>';
}
function highlightIrForLine(line) {
  $$("#ir li").forEach((li) => li.classList.toggle("hl", +li.dataset.line === line));
}
function renderOutline() {
  const a = S.compile?.ast;
  const items = (a?.statements || []).map((s) => s.type === "Function"
    ? `<li data-line="${s.line}">${esc(s.name)}(${s.params.map((p) => esc(p[1])).join(", ")}) <em>line ${s.line}</em></li>`
    : s.type === "VarDecl" ? `<li data-line="${s.line}">${esc(s.var_type)} ${esc(s.name)}${(s.dims || []).map((d) => d && d.value !== undefined ? `[${d.value}]` : "[]").join("")} <em>global</em></li>` : "").join("");
  $("#outline").innerHTML = items || '<li class="muted" style="cursor:default">Nothing to show yet</li>';
}

function renderCfg() {
  S.cfgDirty = false;
  const box = $("#cfg"), cfg = S.compile?.cfg, legend = $("#cfgLegend");
  if (!cfg) { legend.innerHTML = ""; box.innerHTML = '<div class="empty-view">The control-flow graph is built from the IR once the program compiles.</div>'; return; }
  const loops = S.compile.loops || [];
  const inLoop = {}, headers = {}, back = new Set();
  loops.forEach((L) => { L.blocks.forEach((b) => { inLoop[b] = Math.max(inLoop[b] || 0, L.depth); }); headers[L.header] = L; L.back_edges.forEach(([a, h]) => back.add(a + ">" + h)); });
  const reach = new Set(); cfg.edges.forEach(([a, b]) => reach.add(b)); Object.values(cfg.entries || {}).forEach((e) => reach.add(e));
  const cx = S.compile.complexity || {};
  legend.innerHTML = `<span><b>${cfg.blocks.length}</b> basic blocks, <b>${cfg.edges.length}</b> edges</span>`
    + `<span class="loopkey">Amber arrows are back edges: each one makes a loop (${loops.length} found)</span>`
    + Object.entries(cx).map(([f, c]) => `<span>${esc(f)}(): <b>${esc(c.big_o)}</b>${c.recursive ? " + recursion" : ""}</span>`).join("");
  let html = "", lastFn = null;
  cfg.blocks.forEach((b) => {
    if (b.function !== lastFn) { html += `<div class="cfg-fn">${b.function === "<globals>" ? "top-level code (globals)" : esc(b.function) + "()"}</div>`; lastFn = b.function; }
    const L = headers[b.id];
    const cls = ["cfg-block", inLoop[b.id] ? "in-loop" : "", reach.has(b.id) ? "" : "unreach"].join(" ");
    html += `<div class="${cls}" id="cfg-${b.id}"><header>${b.id}${L ? `<span class="loop-tag">loop header, line ${L.header_line}, depth ${L.depth}</span>` : ""}</header><pre>${esc(b.instructions.join("\n")) || " "}</pre></div>`;
  });
  box.innerHTML = html + '<svg class="edges"><defs>' + ["next", "true", "false", "jump", "back"].map((k) => `<marker id="ah-${k}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8z" style="fill:var(--${edgeColor(k)});stroke:none"/></marker>`).join("") + "</defs></svg>";
  requestAnimationFrame(() => {
    const svg = $("svg.edges", box), rect = (id) => { const el = $("#cfg-" + id); return { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight }; };
    const maxRight = Math.max(...cfg.blocks.map((b) => { const r = rect(b.id); return r.x + r.w; }));
    let lane = 0, paths = "";
    const idx = (id) => cfg.blocks.findIndex((x) => x.id === id);
    cfg.edges.forEach(([from, to, kind]) => {
      const isBack = back.has(from + ">" + to), k = isBack ? "back" : kind;
      const a = rect(from), b = rect(to), col = `var(--${edgeColor(k)})`;
      if (idx(to) === idx(from) + 1 && kind !== "jump") {
        const x = a.x + 30; paths += `<path d="M${x},${a.y + a.h} L${x},${b.y - 2}" style="stroke:${col}" marker-end="url(#ah-${k})"/><text x="${x + 6}" y="${a.y + a.h + 15}">${kind === "next" ? "" : kind}</text>`;
      } else {
        const x = maxRight + 18 + (lane++ % 6) * 16, y1 = a.y + a.h / 2, y2 = b.y + 14;
        paths += `<path d="M${a.x + a.w},${y1} H${x} V${y2} H${b.x + b.w + 2}" style="stroke:${col};${isBack ? "stroke-width:2" : ""}" marker-end="url(#ah-${k})"/><text x="${x + 4}" y="${(y1 + y2) / 2}">${isBack ? "back edge" : kind}</text>`;
      }
    });
    svg.insertAdjacentHTML("beforeend", paths);
  });
}
async function renderTrace() {
  S.traceDirty = false;
  const box = $("#trace"), t = tabOf();
  if (!t) return;
  box.innerHTML = '<div class="empty-view">Running the LALR parser…</div>';
  let r;
  try { r = await api("/api/parse_trace", { source: t.model.getValue() }); }
  catch (e) { box.innerHTML = `<div class="empty-view">${esc(e.message)}</div>`; return; }
  const g = r.grammar, a = r.agreement, b = r.bison;
  const pill = (cls, text, title = "") => `<span class="pill ${cls}" title="${esc(title)}">${esc(text)}</span>`;
  let html = `<div class="trace-head">
      ${pill(r.accepted ? "ok" : "bad", r.accepted ? "yacc accepts" : `yacc rejects (${r.errors.length} error${r.errors.length > 1 ? "s" : ""})`)}
      ${pill(a.same ? "ok" : "bad", a.same ? "Hand-written parser agrees" : "Parsers disagree", a.detail)}
      ${pill("y", `${r.trace.length} steps`)}
      ${pill("", `${g.productions.length} productions, ${g.states} LALR states, ${g.conflicts.length} conflicts`)}
    </div>`;
  html += `<div class="bison">${b.available
      ? `<b>flex &amp; bison</b> <span class="muted">grammar/minic.l and grammar/minic.y</span> <button class="mini-btn" id="btnBison">Check this program with flex &amp; bison</button><pre id="bisonOut"></pre>`
      : `<b>flex &amp; bison</b> <span class="muted">not installed (missing ${esc(b.missing.join(", "))}). The grammar files are in grammar/; install them to compare with real yacc. On Windows: choco install winflexbison3.</span>`}</div>`;
  if (r.errors.length) html += `<div class="bison">${r.errors.map((e) => `<p style="margin:2px 0;color:var(--coral)">Line ${e.line}: ${esc(e.message)}</p>`).join("")}</div>`;
  html += `<details><summary>Grammar (${g.productions.length} productions, as in grammar/minic.y)</summary><pre>${esc(g.productions.join("\n"))}</pre></details>`;
  const rows = r.trace.slice(0, 1500);
  html += `<table><colgroup><col class="n"><col class="st"><col><col class="la"><col class="ac"></colgroup><thead><tr><th>#</th><th>State</th><th>Stack</th><th>Lookahead</th><th>Action</th></tr></thead><tbody>${rows.map((x, i) =>
    `<tr class="${x.kind}"><td>${i + 1}</td><td>${esc(x.state)}</td><td class="stack" title="${esc(x.stack)}"><span>${esc(x.stack) || "&nbsp;"}</span></td><td>${esc(x.lookahead)}</td><td class="act" title="${esc(x.action)}">${esc(x.action)}</td></tr>`).join("")}</tbody></table>`;
  if (r.trace.length > rows.length || r.truncated) html += `<p class="muted">Showing the first ${rows.length} steps.</p>`;
  box.innerHTML = html;
  const btn = $("#btnBison");
  if (btn) btn.onclick = async () => {
    btn.disabled = true; $("#bisonOut").textContent = "Building with bison, flex and the C compiler…";
    try {
      const x = await api("/api/bison", { source: t.model.getValue() });
      if (!x.ok) { $("#bisonOut").textContent = x.log; return; }
      $("#bisonOut").textContent = `${x.accepted ? "accept" : "reject"}${x.errors.length ? "\n" + x.errors.map((e) => `line ${e.line}:${e.column} ${e.message}`).join("\n") : ""}
functions: ${(x.functions || []).join(", ") || "-"}   statements: ${x.statements}
${x.agrees ? "Agrees with the hand-written parser." : "Does NOT agree with the hand-written parser."}`;
    } catch (e) { $("#bisonOut").textContent = e.message; }
    finally { btn.disabled = false; }
  };
}
function renderCalls() {
  S.callsDirty = false;
  const box = $("#calls"), cg = S.compile?.call_graph;
  if (!cg || !cg.functions.length) { box.innerHTML = '<div class="empty-view">The call graph appears once the program has functions and parses without errors.</div>'; return; }
  const level = {}, q = cg.functions.includes("main") ? ["main"] : [cg.functions[0]];
  level[q[0]] = 0;
  while (q.length) { const f = q.shift(); (cg.calls[f] || []).forEach((c) => { if (level[c] === undefined) { level[c] = level[f] + 1; q.push(c); } }); }
  const maxL = Math.max(0, ...Object.values(level));
  cg.functions.forEach((f) => { if (level[f] === undefined) level[f] = maxL + 1; });
  const rows = {}; cg.functions.forEach((f) => (rows[level[f]] = rows[level[f]] || []).push(f));
  const cx = S.compile.complexity || {}, pos = {}, W = Math.max(520, box.clientWidth - 40);
  Object.entries(rows).forEach(([l, fs]) => fs.forEach((f, i) => { pos[f] = { x: (W / (fs.length + 1)) * (i + 1), y: 30 + l * 90, w: Math.max(90, f.length * 8 + 40) }; }));
  const H = 30 + (Object.keys(rows).length) * 90;
  let edges = "";
  const seen = new Set();
  cg.edges.forEach(([a, b]) => {
    if (!pos[a] || !pos[b] || seen.has(a + ">" + b)) return; seen.add(a + ">" + b);
    const p = pos[a], r = pos[b];
    if (a === b) edges += `<path class="cg-edge self" d="M${p.x + p.w / 2},${p.y - 10} C${p.x + p.w / 2 + 46},${p.y - 44} ${p.x + p.w / 2 + 46},${p.y + 44} ${p.x + p.w / 2 + 2},${p.y + 10}" fill="none" marker-end="url(#cg-ah)"/>`;
    else edges += `<path class="cg-edge" d="M${p.x},${p.y + 20} C${p.x},${(p.y + r.y) / 2} ${r.x},${(p.y + r.y) / 2} ${r.x},${r.y - 22}" fill="none" marker-end="url(#cg-ah)"/>`;
  });
  const nodes = cg.functions.map((f) => {
    const p = pos[f], cls = ["cg-node", f === "main" ? "main" : "", cg.recursive.includes(f) ? "rec" : "", cg.unreachable.includes(f) ? "dead" : ""].join(" ");
    return `<g class="${cls}" data-line="${cg.line[f]}"><rect x="${p.x - p.w / 2}" y="${p.y - 20}" width="${p.w}" height="40" rx="8"/><text x="${p.x}" y="${p.y - 2}" text-anchor="middle">${esc(f)}()</text><text class="sub" x="${p.x}" y="${p.y + 13}" text-anchor="middle">${esc(cx[f]?.big_o || "O(1)")}${cg.recursive.includes(f) && cx[f]?.big_o !== "recursive" ? ", recursive" : ""}</text></g>`;
  }).join("");
  box.innerHTML = `<svg width="${W}" height="${H}"><defs><marker id="cg-ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8z" style="fill:var(--muted);stroke:none"/></marker></defs>${edges}${nodes}</svg>
    <div class="cg-list">${cg.recursive.length ? `<p>Recursive: <b>${cg.recursive.map(esc).join(", ")}</b> (a call chain leads back to the same function).</p>` : ""}${cg.unreachable.length ? `<p>Never called from main: <b>${cg.unreachable.map(esc).join(", ")}</b> (dashed).</p>` : ""}<p>Click a function to jump to it. Arrows point from caller to callee.</p></div>`;
}
function edgeColor(kind) { return { next: "faint", true: "teal", false: "coral", jump: "blue", back: "sun" }[kind] || "faint"; }

function renderTarget() {
  const b = S.build?.result, info = $("#targetInfo"), pre = $("#targetCode");
  $$(".subtabs button").forEach((x) => x.classList.toggle("active", x.dataset.target === S.targetMode));
  if (!b) { pre.textContent = "Run or build the program to generate target code.\n\nThe back-end turns every IR instruction into one line of C, then a C compiler turns that into a native executable."; info.textContent = ""; return; }
  if (S.targetMode === "c") { pre.textContent = b.c_source || ""; info.textContent = `${b.c_path ? b.c_path.split(/[\\/]/).pop() : ""}, one line per IR instruction`; }
  else { pre.textContent = b.assembly || (b.mode === "vm" ? "No assembly: this program was built for the MiniC VM because no C compiler was found." : "The C compiler did not produce assembly for this build."); info.textContent = b.assembly ? `x86-64/ARM assembly from ${toolShort()} at -O1` : ""; }
}

/* ------------------------------------------------------------- terminal */
function term(text, cls = "out") {
  const t = $("#terminal");
  t.insertAdjacentHTML("beforeend", `<span class="${cls}">${esc(text)}</span>\n`);
  t.parentElement.scrollTop = t.parentElement.scrollHeight;
}
const base = (p) => String(p).split(/[\\/]/).pop();

/* --------------------------------------------------------------- run/build */
async function run() {
  const t = tabOf(); if (!t || S.running) return;
  if (S.debug) stopDebug();
  S.running = true; $("#btnRun").disabled = true;
  setState("busy", "Building…"); setStage("build", "running", "building"); setStage("run", "", "");
  showView("terminal");
  term(`Building ${t.name}`, "note");
  const key = versionKey(), source = t.model.getValue();
  try {
    const stdin = $("#stdinBox").value;
    const r = await api("/api/run", { source, name: t.name, optimize: $("#optO2").checked, frontend: S.frontend, stdin });
    applyCompile(r.compile, key);
    if (!r.compile.success) {
      term(`Compilation failed: ${r.compile.diagnostics.filter((d) => d.severity === "error").map((d) => `line ${d.line}: ${d.message}`).join("; ")}`, "err");
      showView("problems"); aiAfterCompile(r.compile, source); return;
    }
    const b = r.build;
    S.build = { for: key, result: b, run: r.run };
    if (b.command) term(b.command.map((x) => (/[\\/]/.test(x) ? base(x) : x)).join(" "), "cmd");
    if (b.log) term(b.log, b.ok ? "note" : "err");
    if (!b.ok) { renderRail(); setState("fail", "Build failed"); return; }
    const run = r.run;
    term(b.mode === "vm" ? `python ${base(b.exe)}` : (/\.exe$/i.test(b.exe) ? base(b.exe) : "./" + base(b.exe)), "cmd");
    if (r.compile.reads_input) term(stdin.trim() ? `input: ${stdin.trim()}` : "This program reads input with scanf, but the Input box is empty.", stdin.trim() ? "note" : "err");
    (run.stdout || "").replace(/\n$/, "").split("\n").filter((x, i, a) => a.length > 1 || x).forEach((l) => term(l, "out"));
    if (run.stderr) term(run.stderr.trim(), "err");
    if (run.timed_out) term("Stopped after 5 seconds: the program did not finish. Check your loop conditions.", "err");
    else term(`Process exited with code ${run.exit_code} in ${Math.max(1, Math.round(run.seconds * 1000))} ms`, "exit");
    term(`Executable: ${b.exe}`, "note");
    renderRail(); renderTarget();
    setState(run.timed_out || run.stderr ? "fail" : "ok", run.timed_out ? "Timed out" : `Exited with code ${run.exit_code}`);
    aiAfterCompile(r.compile, source);
  } catch (e) { term(e.message, "err"); setState("fail", "Run failed"); setStage("build", "fail", "error"); }
  finally { S.running = false; $("#btnRun").disabled = false; }
}
async function compileClicked() {
  const r = await compileNow();
  if (!r) return;
  term(r.success ? `${S.active}: compiled, ${r.ir.length} IR instructions` : `${S.active}: ${stageFailText(r).toLowerCase()}`, r.success ? "ok" : "err");
  if (!r.success) showView("problems");
  aiAfterCompile(r, tabOf().model.getValue());
}

/* ---------------------------------------------------------------- debugger */
async function startDebug() {
  const t = tabOf(); if (!t) return;
  if (S.debug) { stopDebug(); }
  setState("busy", "Starting debugger…");
  const r = await api("/api/debug", { source: t.model.getValue(), frontend: S.frontend, stdin: $("#stdinBox").value });
  applyCompile(r.compile);
  if (!r.compile.success) { toast("Fix the compile errors before debugging."); showView("problems"); return; }
  if (!r.vm.trace.length) { toast(r.vm.error || "Nothing to step through."); return; }
  S.debug = { trace: r.vm.trace, vm: r.vm, pos: 0, ir: r.compile.ir };
  while (S.debug.pos < S.debug.trace.length - 1 && (S.debug.trace[S.debug.pos].func === "<globals>" && !/^(STORE|CONST|t\d)/.test(S.debug.trace[S.debug.pos].ins) || /^(LABEL|DECLARE|GET_PARAM|PARAM|RESULT_TYPE)/.test(S.debug.trace[S.debug.pos].ins))) S.debug.pos++;
  editor.updateOptions({ readOnly: true });
  $("#debugBar").hidden = false;
  showView("vars");
  setState("debug", "Debugging");
  renderDebug();
}
function stopDebug() {
  if (!S.debug) return;
  S.debug = null;
  editor.updateOptions({ readOnly: false });
  $("#debugBar").hidden = true;
  deco.exec.clear();
  $$("#ir li.cur").forEach((x) => x.classList.remove("cur"));
  $("#vars").innerHTML = '<div class="empty-view">Start the debugger (F9) to see variables change step by step.</div>';
  if (S.compile) setState(S.compile.success ? "ok" : "fail", S.compile.success ? "Compiled" : stageFailText(S.compile));
}
function dbg(action) {
  const d = S.debug; if (!d) return;
  const n = d.trace.length, lineAt = (p) => d.trace[p].line, depth = (p) => d.trace[p].depth || 1;
  const noop = (p) => /^(LABEL|DECLARE|PARAM|RESULT_TYPE)/.test(d.trace[p].ins) || d.trace[p].func === "<globals>" && d.trace[p].ret !== undefined;
  const settle = (p) => { while (p < n - 1 && noop(p)) p++; return p; };
  const d0 = depth(d.pos), l0 = lineAt(d.pos);
  let p = d.pos + 1;
  if (action === "ins") d.pos = Math.min(n - 1, d.pos + 1);
  else if (action === "line") {          // step into
    while (p < n - 1 && ((lineAt(p) === l0 && depth(p) === d0) || noop(p))) p++;
    d.pos = Math.min(p, n - 1);
  } else if (action === "over") {
    while (p < n - 1 && (depth(p) > d0 || (lineAt(p) === l0 && depth(p) === d0) || noop(p))) p++;
    d.pos = Math.min(p, n - 1);
  } else if (action === "out") {
    while (p < n - 1 && depth(p) >= d0) p++;
    d.pos = settle(Math.min(p, n - 1));
  } else if (action === "back") {
    p = d.pos - 1;
    while (p > 0 && (lineAt(p) === l0 && depth(p) === d0 || noop(p))) p--;
    const l2 = lineAt(Math.max(p, 0)), dd = depth(Math.max(p, 0));
    while (p > 0 && lineAt(p - 1) === l2 && depth(p - 1) === dd) p--;
    d.pos = Math.max(0, p);
  } else if (action === "cont") {
    const bps = new Set(S.breakpoints[S.active] || []);
    while (p < n - 1 && !(bps.has(lineAt(p)) && (lineAt(p) !== lineAt(p - 1) || depth(p) !== depth(p - 1)))) p++;
    d.pos = settle(Math.min(p, n - 1));
  } else if (action === "restart") d.pos = settle(0);
  else if (action === "stop") return stopDebug();
  renderDebug();
}
function replay(d) {
  /* Rebuild call stack, globals and output up to d.pos from the trace. */
  const globals = {}, out = [];
  let frames = [], changed = null, callLine = null;
  for (let i = 0; i <= d.pos; i++) {
    const e = d.trace[i], dep = e.depth || 1;
    if (frames.length > dep) frames.length = dep;
    while (frames.length < dep) frames.push({ func: e.func, env: {}, line: callLine });
    if (frames[dep - 1].func !== e.func) frames[dep - 1] = { func: e.func, env: {}, line: callLine };
    frames[dep - 1].cur = e.line;
    if (e.changed) { (e.changed[2] === "g" ? globals : frames[dep - 1].env)[e.changed[0]] = e.changed[1]; if (i === d.pos) changed = e.changed[0]; }
    if (e.text !== null && e.text !== undefined) out.push(e.text);
    else if (e.out !== null && e.out !== undefined) out.push(e.out + "\n");
    if (e.call) callLine = e.line;
    if (e.ret !== undefined) {
      frames.length = dep - 1;
      if (e.dest) { (e.dest[2] === "g" ? globals : (frames[dep - 2] || { env: globals }).env)[e.dest[0]] = e.dest[1]; if (i === d.pos) changed = e.dest[0]; }
    }
  }
  return { frames: frames.filter((f) => f.func !== "<globals>"), globals, out, changed };
}
function renderDebug() {
  const d = S.debug; if (!d) return;
  const ev = d.trace[d.pos];
  const { frames, globals, out, changed } = replay(d);
  if (ev.line) {
    deco.exec.set([{ range: new monaco.Range(ev.line, 1, ev.line, 1), options: { isWholeLine: true, className: "dbg-line", glyphMarginClassName: "dbg-glyph" } }]);
    editor.revealLineInCenterIfOutsideViewport(ev.line);
  }
  $$("#ir li").forEach((li) => li.classList.toggle("cur", +li.dataset.i === ev.i));
  $("#ir li.cur")?.scrollIntoView({ block: "nearest" });
  const end = d.pos === d.trace.length - 1;
  $("#dbgPos").textContent = `step ${d.pos + 1} of ${d.trace.length}, line ${ev.line ?? "?"}`;
  const isT = (k) => /^t\d+$/.test(k);
  const row = (k, v) => `<tr class="${k === changed ? "changed" : ""} ${isT(k) ? "temp" : ""}"><td>${esc(k)}</td><td>${esc(v)}</td></tr>`;
  const table = (env, filter) => { const ks = Object.keys(env).filter(filter).sort((a, b) => isT(a) && isT(b) ? +a.slice(1) - +b.slice(1) : a < b ? -1 : 1); return `<table>${ks.map((k) => row(k, env[k])).join("") || '<tr><td colspan="2" class="muted">none yet</td></tr>'}</table>`; };
  const top = frames[frames.length - 1] || { func: ev.func, env: {} };
  const stack = frames.slice().reverse().map((f, i) => `<li class="${i === 0 ? "top" : ""}">${esc(f.func)}()${f.cur ? ` at line ${f.cur}` : ""}${f.line && i < frames.length - 1 ? "" : ""}</li>`).join("");
  let finish = "";
  if (end) {
    finish = d.vm.error ? `<p style="color:var(--coral)">Runtime error: ${esc(d.vm.error)}${d.vm.error_line ? ` (line ${d.vm.error_line})` : ""}</p>` : `<p style="color:var(--teal)">Program finished. main returned ${esc(d.vm.return_value)}.</p>`;
    if (d.vm.trace_truncated) finish += '<p class="muted">The trace was cut short because the program runs for a very long time.</p>';
  }
  const gl = Object.keys(globals).length ? `<h4 style="margin-top:12px">Globals</h4>${table(globals, () => true)}` : "";
  $("#vars").innerHTML = `<div><h4>Call stack</h4><ul class="stack">${stack || "<li class=\"top\">(starting up)</li>"}</ul>
      <h4 style="margin-top:12px">Variables in ${esc(top.func)}()</h4>${table(top.env, (k) => !isT(k))}${gl}
      <h4 style="margin-top:12px">IR temporaries</h4>${table(Object.fromEntries(Object.entries(top.env).filter(([k]) => isT(k)).slice(-8)), () => true)}</div>
    <div><h4>Just ran</h4><pre class="outbox">${esc(ev.ins)}${ev.ret !== undefined && ev.func !== "<globals>" ? `\n(returns ${esc(ev.ret)} to the caller)` : ""}</pre><h4 style="margin-top:12px">Output so far</h4><pre class="outbox">${esc(out.join(""))}</pre>${finish}</div>`;
}
function toggleBreakpoint(line) {
  const list = new Set(S.breakpoints[S.active] || []);
  list.has(line) ? list.delete(line) : list.add(line);
  S.breakpoints[S.active] = [...list];
  store.set("breakpoints", S.breakpoints);
  renderBreakpoints();
}
function renderBreakpoints() {
  deco.bp.set((S.breakpoints[S.active] || []).map((l) => ({ range: new monaco.Range(l, 1, l, 1), options: { glyphMarginClassName: "bp-glyph", glyphMarginHoverMessage: { value: "Breakpoint: Continue stops here" } } })));
}

/* ----------------------------------------------------------------------- AI */
async function checkAi(silent = false) {
  const want = store.get("model", null) || S.info?.default_model;
  try {
    const s = await api(`/api/ai/status${want ? "?model=" + encodeURIComponent(want) : ""}`);
    S.ai.ok = s.ok; S.ai.model = s.model; S.ai.models = s.models;
    const sel = $("#modelSelect");
    if (s.models.length) {
      const opts = s.models.includes(s.model) ? s.models : [s.model, ...s.models];
      sel.innerHTML = opts.map((m) => `<option ${m === s.model ? "selected" : ""} value="${esc(m)}">${esc(m)}${s.models.includes(m) ? "" : " (not installed)"}</option>`).join("");
    } else sel.innerHTML = `<option>Ollama is not running</option>`;
    $("#aiDot").className = "dot " + (s.ok ? "ok" : "bad");
    $("#aiState").textContent = s.ok ? "ready" : s.models.length ? "model missing" : "offline";
    $("#aiState").title = s.error || "";
    sel.parentElement.title = s.error || `Ollama at ${s.host}`;
    if (!s.ok && !silent) toast(s.error);
    if (s.ok) api("/api/ai/warmup", { model: s.model }).catch(() => {});   // load the model now, not on the first click
  } catch (e) { if (!silent) toast(e.message); }
  updateAiButtons();
}
function updateAiButtons() {
  const ok = S.ai.ok, r = S.compile;
  const set = (id, on, why) => { const b = $(id); b.disabled = !on; b.title = on ? "" : why; };
  set("#aiFix", ok && r && !r.success, !ok ? "Start Ollama to use the AI assistant" : "Your code compiles: nothing to fix");
  set("#aiOpt", ok && r && r.success, !ok ? "Start Ollama to use the AI assistant" : "Fix the compile errors first");
  set("#aiDocs", ok && !!tabOf(), "Start Ollama to use the AI assistant");
  const compiled = r && r.success;
  set("#aiBugs", compiled, "Fix the compile errors first");
  set("#aiRefactor", compiled, "Fix the compile errors first");
  set("#aiFlow", compiled, "Fix the compile errors first");
}

const FEATURE_TITLE = { fix: "Fixes", optimize: "Optimisation", docs: "Documentation", bugs: "Bugs", bugfix: "Full bug fix", refactor: "Refactoring", flow: "Flow and complexity" };
function newCard(title) {
  $(".ai-empty")?.remove();
  const el = document.createElement("article");
  el.className = "card busy";
  el.innerHTML = `<div class="card-head"><h3>${esc(title)}</h3><span class="badge wait"><span class="spinner"></span> <span class="secs">0 s</span></span></div><div class="card-body"><p class="muted">${S.ai.ok ? `Asking ${esc(S.ai.model)}. Local models can take a minute.` : "Running the compiler's own analysis (Ollama is offline)."}</p></div>`;
  $("#aiFeed").prepend(el);
  $("#aiFeed").scrollTop = 0;
  const t0 = Date.now(); el._timer = setInterval(() => { const s = $(".secs", el); if (s) s.textContent = `${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
  return el;
}
/* Live status lines on a card while the model works: one line per attempt. */
function liveStatus(card) {
  const body = $(".card-body", card);
  body.innerHTML = `<div class="partial"></div><ol class="live"></ol><p class="muted live-note">${S.ai.ok ? "The first answer can take a while on a laptop: the model writes it word by word." : ""}</p>`;
  const list = $(".live", body), t0 = Date.now();
  const lineFor = (n) => {
    let li = list.querySelector(`li[data-a="${n}"]`);
    if (!li) { li = document.createElement("li"); li.dataset.a = n; list.appendChild(li); }
    return li;
  };
  return (e) => {
    const li = lineFor(e.attempt), secs = `${Math.round((Date.now() - t0) / 1000)} s`;
    if (e.type === "partial") {               // the compiler's own results, before the model has answered
      const tmp = document.createElement("article");
      tmp.innerHTML = '<div class="card-head"><span class="badge"></span></div><div class="card-body"></div>';
      renderAnalysisCard(tmp, "bugs", e.report, {});
      $(".partial", body).innerHTML = $(".card-body", tmp).innerHTML;
      $(".live-note", body).textContent = "These come from the compiler and a test run. The AI's explanations follow.";
      return;
    }
    if (e.type === "ask") { li.className = "wait"; li.innerHTML = `<b>Attempt ${e.attempt}</b> asking the model…`; }
    else if (e.type === "progress") {
      li.innerHTML = e.chars ? `<b>Attempt ${e.attempt}</b> the model is writing: ${e.chars.toLocaleString()} characters <small>${secs}</small>`
                             : `<b>Attempt ${e.attempt}</b> the model is thinking… <small>${secs}</small>`;
    }
    else if (e.type === "checking") { li.innerHTML = `<b>Attempt ${e.attempt}</b> checking the answer with the compiler…`; }
    else if (e.type === "check") {
      li.className = e.passed ? "ok" : "no";
      li.innerHTML = `<b>Attempt ${e.attempt}</b> ${e.passed ? "accepted by the compiler" : "rejected: " + esc((e.notes || []).slice(-1)[0] || "")}${e.passed ? "" : " <small>asking again with the reason</small>"}`;
    }
  };
}
async function runAi(feature, { source, correctedFrom = null, title, fresh = false } = {}) {
  const t = tabOf(); if (!t) return null;
  source = source ?? t.model.getValue();
  const fileName = t.name, card = newCard(title || `${FEATURE_TITLE[feature]} for ${fileName}`);
  S.ai.busy++; $("#aiState").textContent = "working…";
  const live = liveStatus(card);
  try {
    const rep = await apiStream("/api/ai", { feature, source, corrected_from: correctedFrom, model: S.ai.model, stdin: $("#stdinBox").value || null, fresh }, live);
    renderCard(card, feature, rep, { source, fileName, correctedFrom });
    if (rep.cached) $(".card-head h3", card).insertAdjacentHTML("beforeend", ' <span class="cached" title="The same question was answered before, so the verified answer was reused">instant: answered before</span>');
    return rep;
  } catch (e) {
    card.classList.remove("busy");
    $(".card-head .badge", card).outerHTML = '<span class="badge err">failed</span>';
    $(".card-body", card).innerHTML = `<p>${esc(e.message)}</p>`;
    return null;
  } finally {
    clearInterval(card._timer);
    S.ai.busy--; $("#aiState").textContent = S.ai.busy ? "working…" : S.ai.ok ? "ready" : "offline";
  }
}
/* ----------------------------------------------------------- the referee */
const REF_FEATURE = { fix: "Fix errors", optimize: "Optimise", docs: "Document", bugs: "Find bugs" };
async function runReferee() {
  const t = tabOf(); if (!t) return;
  const source = t.model.getValue(), fileName = t.name;
  const card = newCard(`Referee test for ${fileName}`);
  $(".card-body", card).innerHTML = '<p class="muted">Building wrong answers from your program and judging each one…</p>';
  try {
    const res = await api("/api/referee/challenge", { source, stdin: $("#stdinBox").value || null });
    clearInterval(card._timer); card.classList.remove("busy");
    const cases = res.cases;
    const wrong = cases.filter((c) => c.expect === "reject" && !c.skipped), good = cases.filter((c) => c.expect === "accept" && !c.skipped);
    const caught = wrong.filter((c) => !c.passed).length, harmless = wrong.length - caught, okd = good.filter((c) => c.passed).length;
    $(".card-head .badge", card).outerHTML = `<span class="badge ok">${caught} caught</span>`;
    const row = (c, i) => {
      if (c.skipped) return "";
      const st = c.passed ? (c.expect === "reject" ? "same" : "ok") : "no";
      const word = { ok: "Accepted", no: "Rejected", same: "Accepted: no visible difference" }[st];
      const why = c.passed && c.expect === "reject"
        ? "This change does not alter anything the program prints or returns (for example it is in code that never runs), so it is harmless here."
        : c.notes[c.notes.length - 1];
      return `<li class="${st}"><b>${esc(c.label)}</b><span class="chip-f">${REF_FEATURE[c.feature]}</span>
        <small>${esc(c.change)}</small><span><em>${word}:</em> ${esc(why)}</span>
        <button class="mini-btn" data-act="refcase" data-k="${i}">See this answer</button></li>`;
    };
    $(".card-body", card).innerHTML = `<p>${wrong.length} wrong answers were built from your program: the compiler rejected <b>${caught}</b>${harmless ? `, and let ${harmless} through because ${harmless > 1 ? "they make" : "it makes"} no visible difference` : ""}. ${good.length} correct answer${good.length !== 1 ? "s were" : " was"} built too: ${okd} accepted.</p>
      <ol class="refcases">${cases.map(row).join("")}</ol>
      <p class="muted">Every verdict comes from the same checks the AI's real answers go through: a full compile, a token comparison, or a run on the VM${$("#stdinBox").value ? " with your Input box text" : ""}.</p>`;
    card._cases = cases; card._ctx = { source, fileName };
    const foot = document.createElement("div"); foot.className = "card-foot";
    foot.innerHTML = '<button class="btn primary" data-act="reftry">Try to fool it yourself</button><button class="btn" data-act="refagain">Run again</button>';
    card.append(foot);
  } catch (e) {
    clearInterval(card._timer); card.classList.remove("busy");
    $(".card-head .badge", card).outerHTML = '<span class="badge err">failed</span>';
    $(".card-body", card).innerHTML = `<p>${esc(e.message)}</p>`;
  }
}
function openRefereeEditor(source, proposed, feature, title) {
  openDiff({ title: title || "Write an answer and let the referee judge it", original: source, proposed, verified: true, by: "you (pretending to be the AI)",
    note: "Edit the right-hand side, then submit it." });
  diffEditor.updateOptions({ readOnly: false });
  const sel = $("#refFeature"); sel.hidden = false; sel.value = feature;
  const acc = $("#diffAccept"); acc.textContent = "Submit to the referee"; acc.classList.remove("risky");
  diffPending = { referee: true, source };
}
async function submitReferee() {
  const proposed = diffModels.modified.getValue(), feature = $("#refFeature").value, n = $("#diffNote");
  n.textContent = "Judging…"; n.className = "diff-note";
  try {
    const v = await api("/api/referee/judge", { source: diffPending.source, feature, candidate: proposed, stdin: $("#stdinBox").value || null });
    if (v.skipped) { n.textContent = v.skipped; n.className = "diff-note warn"; return; }
    n.textContent = (v.passed ? "Accepted: " : "Rejected: ") + v.notes.join("; ");
    n.className = "diff-note " + (v.passed ? "pass" : "fail");
    const card = newCard(`Your ${REF_FEATURE[feature]} answer`);
    clearInterval(card._timer); card.classList.remove("busy");
    $(".card-head .badge", card).outerHTML = v.passed ? '<span class="badge ok">accepted</span>' : '<span class="badge err">rejected</span>';
    $(".card-body", card).innerHTML = `<ul>${v.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  } catch (e) { n.textContent = e.message; n.className = "diff-note warn"; }
}
function prettyTechnique(t) { return String(t || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()); }
function renderCard(card, feature, rep, ctx) {
  card.classList.remove("busy");
  const badge = $(".card-head .badge", card);
  if (rep.skipped) { badge.outerHTML = '<span class="badge">skipped</span>'; $(".card-body", card).innerHTML = `<p>${esc(rep.skipped)}</p>`; return; }
  if (feature === "bugfix") feature = "bugs";
  if (feature === "bugs" || feature === "refactor" || feature === "flow") return renderAnalysisCard(card, feature, rep, ctx);
  const r = rep.result, v = rep.verification;
  badge.outerHTML = v.passed ? `<span class="badge ok" title="Checked by the compiler">verified</span>` : `<span class="badge bad" title="Did not pass the compiler's check">not verified</span>`;
  let body = "", proposed = "", applyLabel = "Review and apply", newSuffix = "";
  const line = (n) => n ? `<span class="where" data-line="${n}">Line ${n}</span>` : "";
  if (feature === "fix") {
    proposed = r.fixed_code; newSuffix = "fixed";
    body = `<p>${esc(r.summary)}</p>` + (r.fixes || []).map((f) => `<div class="item">${line(f.line)}${f.source === "found_by_ai" ? '<span class="tag">found by the AI</span>' : ""}
        <p class="muted">${esc(f.error)}</p><p>${esc(f.why)}</p>${f.before ? `<pre class="before">${esc(f.before.trim())}</pre>` : ""}${f.after ? `<pre class="after">${esc(f.after.trim())}</pre>` : ""}</div>`).join("")
      + (r.lesson ? `<div class="lesson">${esc(r.lesson)}</div>` : "");
  } else if (feature === "optimize") {
    proposed = r.optimized_code; newSuffix = "optimized";
    body = `<p>${esc(r.summary)}</p>` + ((r.suggestions || []).map((s) => `<div class="item">${line(s.line)} <span class="impact-${esc(s.impact)}">${esc(prettyTechnique(s.technique))}, ${esc(s.impact)} impact</span>
        <p>${esc(s.explanation)}</p>${s.before ? `<pre class="before">${esc(s.before.trim())}</pre>` : ""}${s.after ? `<pre class="after">${esc(s.after.trim())}</pre>` : ""}${s.safe_because ? `<p class="muted">Safe because ${esc(s.safe_because.replace(/^\w/, (c) => c.toLowerCase()))}</p>` : ""}</div>`).join("") || "<p class=\"muted\">No worthwhile optimisations found.</p>");
  } else {
    proposed = r.documented_code; newSuffix = ctx.correctedFrom ? "fixed" : "documented";
    if (ctx.correctedFrom) applyLabel = "Review corrected code";
    body = `<p>${esc(r.overview)}</p>` + (r.concepts?.length ? `<div class="chips">${r.concepts.map((c) => `<span>${esc(c)}</span>`).join("")}</div>` : "");
  }
  body += refereeLog(v) + `<details><summary>${v.passed ? "Checked by the compiler" : "Why it is not verified"} (${v.attempts} attempt${v.attempts > 1 ? "s" : ""})</summary><ul>${v.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>`;
  $(".card-body", card).innerHTML = body;
  const foot = document.createElement("div");
  foot.className = "card-foot";
  foot.innerHTML = `<button class="btn primary" data-act="review">${applyLabel}</button><button class="btn" data-act="new">Open as new file</button><button class="btn" data-act="again" title="Ask the model again (skips the saved answer)">Ask again</button>`;
  card.append(foot);
  card._ctx = { ...ctx, proposed, verified: v.passed, feature, newSuffix };
}
/* The referee's record: shown whenever the compiler rejected at least one AI answer. */
function refereeLog(v) {
  const log = (v && v.log) || [];
  const rejected = log.filter((e) => !e.passed).length;
  if (!rejected) return "";
  const head = v.passed
    ? `The compiler rejected ${rejected} answer${rejected > 1 ? "s" : ""} before accepting this one`
    : `The compiler rejected all ${rejected} answer${rejected > 1 ? "s" : ""}`;
  return `<div class="referee"><div class="ref-head">${head}</div><ol>${log.map((e) => `<li class="${e.passed ? "ok" : "no"}">
      <b>Attempt ${e.attempt}: ${e.passed ? "accepted" : "rejected"}</b><span>${esc(e.notes.join("; "))}</span>
      ${e.passed ? "" : `<small>Sent back to the model: ${esc((e.feedback || "").split("\n")[0].slice(0, 160))}</small>`}</li>`).join("")}</ol></div>`;
}
function verifBlock(v, label) {
  if (!v) return "";
  return refereeLog(v) + `<details><summary>${v.passed ? (label || "Checked by the compiler") : "Why it is not verified"} (${v.attempts} attempt${v.attempts > 1 ? "s" : ""})</summary><ul>${v.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>`;
}
function renderAnalysisCard(card, feature, rep, ctx) {
  const badge = $(".card-head .badge", card), v = rep.verification, r = rep.result || {};
  const line = (n) => n ? `<span class="where" data-line="${n}">Line ${n}</span>` : "";
  const aiNote = rep.ai_error ? `<p class="note">AI explanation unavailable (${esc(rep.ai_error.split(".")[0])}). Showing the compiler's own analysis.</p>` : "";
  let body = aiNote, proposed = null, newSuffix = feature === "flow" ? "faster" : feature === "bugs" ? "bugfixed" : "refactored", verified = true;
  if (feature === "bugs") {
    const ai = Object.fromEntries((r.findings || []).map((f) => [f.id, f]));
    const fs = rep.findings || [];
    const run = rep.run || {};
    const real = fs.filter((f) => f.confirmed || ["real_bug", "likely_bug"].includes(ai[f.id]?.verdict)).length;
    badge.outerHTML = `<span class="badge ${real ? "err" : "ok"}">${real ? `${real} bug${real > 1 ? "s" : ""}` : "no bugs found"}</span>`;
    body += `<p>${esc(r.summary || (fs.length ? `${fs.length} possible problem${fs.length > 1 ? "s" : ""} found.` : "No rule fired and the program ran without problems."))}</p>`;
    body += `<p class="note">${run.ran ? `Test run: ${run.error ? `stopped with "${esc(run.error)}" at line ${run.error_line}` : `finished, returned ${esc(run.return_value)}`}.` : esc(run.reason || "")}</p>`;
    body += fs.map((f) => {
      const a = ai[f.id] || {};
      const verdict = a.verdict ? `<span class="tagc ${a.verdict === "not_a_bug" ? "no" : "ai"}">AI: ${esc(a.verdict.replace(/_/g, " "))}</span>` : "";
      return `<div class="finding"><div class="ftitle"><span class="sev ${f.severity}"></span>${line(f.line)}<strong>${esc(f.title)}</strong>
        ${f.confirmed ? '<span class="tagc run">confirmed by running</span>' : `<span class="tagc rule">${f.source === "execution" ? "found by running" : "rule"}</span>`}${verdict}</div>
        <p>${esc(a.explanation || f.message)}</p>${f.evidence ? `<p class="ev">${esc(f.evidence)}</p>` : ""}${a.fix ? `<pre class="after">${esc(a.fix.trim())}</pre>` : f.fix_hint ? `<p class="ev"><b>How to fix:</b> ${esc(f.fix_hint)}</p>` : ""}</div>`;
    }).join("");
    body += (r.extra || []).map((e) => `<div class="finding"><div class="ftitle"><span class="sev medium"></span>${line(e.line)}<strong>${esc(e.title)}</strong><span class="tagc ai">AI suspects</span></div><p>${esc(e.explanation)}</p>${e.fix ? `<pre class="after">${esc(e.fix.trim())}</pre>` : ""}</div>`).join("");
    if (r.fixed_code) { proposed = r.fixed_code; verified = v ? v.passed : false; body += verifBlock(v, "Fix checked by compiling and running"); }
  } else if (feature === "refactor") {
    const items = rep.items || [];
    badge.outerHTML = `<span class="badge ${items.length ? "ok" : ""}">${items.length} change${items.length === 1 ? "" : "s"}</span>`;
    body += `<p>${esc(rep.summary || (items.length ? "The compiler rewrote these parts of the syntax tree. Each change was checked by running the program." : "No refactoring patterns matched."))}</p>`;
    body += items.map((it, k) => `<div class="finding"><div class="ftitle">${line(it.line)}<strong>${esc(it.title)}</strong>
        <span class="tagc ${it.verified ? "ok" : "run"}">${it.verified ? "same behaviour" : "not verified"}</span></div>
        <p>${esc(it.ai_explanation || it.explanation)}</p><pre class="before">${esc(it.before_display || it.before)}</pre><pre class="after">${esc(it.after_display || it.after)}</pre>
        ${it.comments_lost ? '<p class="ev">Comments inside this part are not kept.</p>' : ""}
        <p class="ev">${esc((it.notes || []).join("; "))}</p><button class="mini-btn" data-act="item" data-k="${k}">Review this change</button></div>`).join("");
    if (rep.tip) body += `<div class="lesson">${esc(rep.tip)}</div>`;
    if (rep.all_code && rep.all_verified) proposed = rep.all_code;
    else if (items.length === 1 && items[0].verified) proposed = items[0].after_code;
    card._items = items;
  } else {
    const facts = rep.facts || {}, cx = facts.complexity || {};
    const worst = Object.entries(cx).sort((a, b) => (b[1].big_o.length - a[1].big_o.length))[0];
    badge.outerHTML = r.improved_code ? (v.passed ? '<span class="badge ok">verified</span>' : '<span class="badge bad">not verified</span>') : `<span class="badge">${rep.result ? "explained" : "compiler facts"}</span>`;
    body += `<div class="bigo"><b>${esc(r.complexity?.big_o || (worst ? worst[1].big_o : "O(1)"))}</b><span class="muted">${esc(r.complexity?.reasoning || (worst ? `estimated from the loops in ${worst[0]}()` : "no loops"))}</span></div>`;
    if (r.overview) body += `<p>${esc(r.overview)}</p>`;
    const loops = r.loops?.length ? r.loops : (facts.loops || []).map((L) => ({ line: L.header_line, runs: "", explanation: `Loop in ${L.function}(), nesting depth ${L.depth}.` }));
    body += loops.map((L) => `<div class="item">${line(L.line)} <span class="muted">${esc(L.runs ? "runs " + L.runs : "")}</span><p>${esc(L.explanation)}</p></div>`).join("");
    if (r.functions?.length) body += `<div class="item"><p class="muted">Functions</p>${r.functions.map((f) => `<p><b>${esc(f.name)}()</b> ${esc(f.purpose)}</p>`).join("")}</div>`;
    body += (r.hints || []).map((h) => `<div class="finding"><div class="ftitle">${line(h.line)}<strong>${esc(h.issue)}</strong><span class="tagc ai">${esc(h.before || "?")} to ${esc(h.after || "?")}</span></div><p>${esc(h.suggestion)}</p></div>`).join("");
    if (!rep.result) body += `<details open><summary>What the compiler found</summary><ul>${(rep.structure || []).map((x) => `<li>${esc(x.replace(/^- /, ""))}</li>`).join("")}</ul></details>`;
    if (r.improved_code) { proposed = r.improved_code; verified = v ? v.passed : false; body += verifBlock(v, "Faster version checked by running"); }
  }
  $(".card-body", card).innerHTML = body;
  const wantsFix = feature === "bugs" && !proposed && !rep.full_fix && rep.result && (rep.findings || []).some((f) => f.confirmed || f.severity === "high");
  if (proposed || wantsFix) {
    const foot = document.createElement("div");
    foot.className = "card-foot";
    const label = { bugs: "Review bug fixes", refactor: (rep.items || []).length > 1 ? "Review all changes" : "Review change", flow: "Review faster version" }[feature];
    foot.innerHTML = proposed ? `<button class="btn primary" data-act="review">${label}</button><button class="btn" data-act="new">Open as new file</button>`
      : `<button class="btn primary" data-act="fullfix" title="The model writes the whole corrected program; it is compiled and run before you see it. Slower.">Propose a full fix</button>`;
    card.append(foot);
  }
  card._ctx = { ...ctx, proposed, verified, feature, newSuffix };
}
function cardAction(card, act, btn) {
  if (act === "refagain") { runReferee(); return; }
  if (act === "fullfix") {
    const c = card._ctx;
    if (S.active !== c.fileName && tabOf(c.fileName)) activate(c.fileName);
    runAi("bugfix", { source: c.source, title: `Full bug fix for ${c.fileName}` });
    return;
  }
  if (act === "again") {
    const c = card._ctx;
    if (S.active !== c.fileName && tabOf(c.fileName)) activate(c.fileName);
    runAi(c.feature, { source: c.source, correctedFrom: c.correctedFrom, fresh: true });
    return;
  }
  if (act === "reftry" || act === "refcase") {
    const c = card._ctx, cur = tabOf(c.fileName); if (!cur) { toast(`${c.fileName} is closed.`); return; }
    if (S.active !== c.fileName) activate(c.fileName);
    const k = act === "refcase" ? card._cases[+btn.dataset.k] : null;
    const feature = k ? k.feature : (S.compile && S.compile.success ? "optimize" : "fix");
    openRefereeEditor(c.source, k ? k.code : c.source, feature, k ? `${k.label} (you can edit it and resubmit)` : null);
    return;
  }
  if (act === "item") {
    const it = card._items[+btn.dataset.k];
    card._ctx = { ...card._ctx, proposed: it.after_code, verified: !!it.verified, feature: "refactor" };
    act = "review";
  }
  const c = card._ctx; if (!c) return;
  if (act === "new") {
    const stem = c.fileName.replace(/\.c$/, "").replace(/\.(fixed|optimized|documented|bugfixed|refactored|faster)\d*$/, "");
    newFile(freeName(stem, c.newSuffix), c.proposed.endsWith("\n") ? c.proposed : c.proposed + "\n").then((n) => n && toast(`Created ${n}`));
    return;
  }
  if (S.active !== c.fileName) { const t = tabOf(c.fileName); if (!t) { toast(`${c.fileName} is closed.`); return; } activate(c.fileName); }
  const current = tabOf().model.getValue();
  const stale = current !== c.source ? "Your code changed after this suggestion was made." : "";
  openDiff({
    title: { fix: "Review fixes", optimize: "Review optimisation", docs: c.correctedFrom ? "Review corrected and documented code" : "Review documentation",
             bugs: "Review bug fixes", refactor: "Review refactoring", flow: "Review faster version" }[c.feature],
    original: current, proposed: c.proposed, verified: c.verified, by: c.feature === "refactor" ? "the compiler (refactoring)" : "the AI",
    note: [!c.verified ? "This change did not pass the compiler's check." : "Checked by the compiler.", stale].filter(Boolean).join(" "),
  });
}

async function aiAfterCompile(r, source) {
  if (!$("#aiAuto").checked || !S.ai.ok || !r) return;
  if (S.ai.pipeline) { toast("The AI is still working on the previous request."); return; }
  S.ai.pipeline = true;
  try {
    if (r.success) { await runAi("docs", { source, title: `Documentation for ${S.active}` }); return; }
    const fix = await runAi("fix", { source });
    await runAi("docs", { source, title: "Your code, with each error explained" });
    const corrected = fix?.result?.fixed_code;
    if (corrected) await runAi("docs", { source: corrected, correctedFrom: source, title: "Corrected code, with each fix marked" });
  } finally { S.ai.pipeline = false; }
}

/* -------------------------------------------------------------------- diff */
let diffModels = null, diffPending = null;
function openDiff({ title, original, proposed, verified, note, by = "the AI" }) {
  $("#diffRightLabel").textContent = `Proposed by ${by}`;
  if (S.debug) stopDebug();
  if (!diffEditor) {
    diffEditor = monaco.editor.createDiffEditor($("#diffEditor"), { theme: "minic", readOnly: true, originalEditable: false, automaticLayout: true,
      fontFamily: "Overpass Mono, Consolas, monospace", fontSize: 13, minimap: { enabled: false }, renderOverviewRuler: false, scrollBeyondLastLine: false });
  }
  diffModels?.original.dispose(); diffModels?.modified.dispose();
  diffModels = { original: monaco.editor.createModel(original, "minic"), modified: monaco.editor.createModel(proposed, "minic") };
  diffEditor.setModel(diffModels);
  const wide = $(".editor-wrap").offsetWidth > 820;
  diffEditor.updateOptions({ renderSideBySide: wide, useInlineViewWhenSpaceIsLimited: false });
  $(".diff-labels").classList.toggle("inline", !wide);
  $("#diffTitle").textContent = title;
  const n = $("#diffNote"); n.textContent = note; n.className = "diff-note" + (verified ? "" : " warn");
  const acc = $("#diffAccept"); acc.textContent = verified ? "Apply to file" : "Apply anyway"; acc.classList.toggle("risky", !verified);
  diffPending = { proposed };
  $("#diffView").hidden = false;
  acc.focus();
}
function closeDiff() {
  $("#diffView").hidden = true; diffPending = null; $("#refFeature").hidden = true;
  diffEditor?.updateOptions({ readOnly: true }); editor.focus();
}
async function acceptDiff() {
  if (!diffPending) return;
  if (diffPending.referee) return submitReferee();
  const t = tabOf(), text = diffPending.proposed.endsWith("\n") ? diffPending.proposed : diffPending.proposed + "\n";
  t.model.pushEditOperations([], [{ range: t.model.getFullModelRange(), text }], () => null);
  t.model.pushStackElement();
  closeDiff();
  try {
    const r = await saveActive(true);
    toast(r.backup ? `Applied and saved. Your previous version is in ${r.backup}` : "Applied and saved.");
  } catch (e) { toast("Applied, but saving failed: " + e.message); }
  compileNow();
}

/* --------------------------------------------------------------------- UI */
function setState(kind, text) { const el = $("#sbState"); el.className = "sb-state " + kind; el.textContent = text; document.body.dataset.verdict = kind; }
let toastTimer;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3800); }
function toggleAiPanel(force) {
  const show = force ?? $(".main").classList.contains("no-ai");
  $(".main").classList.toggle("no-ai", !show);
  $("#toggleAI").classList.toggle("active", show);
  store.set("aiHidden", !show);
}

function wireUi() {
  $("#btnRun").onclick = run;
  $("#btnCompile").onclick = compileClicked;
  $("#btnDebug").onclick = () => startDebug().catch((e) => toast(e.message));
  $("#optO2").onchange = (e) => store.set("optO2", e.target.checked);
  const ps = $("#parserSelect");
  ps.value = S.frontend; ps.parentElement.classList.toggle("yacc", S.frontend === "yacc");
  ps.onchange = () => {
    S.frontend = ps.value; store.set("frontend", S.frontend);
    ps.parentElement.classList.toggle("yacc", S.frontend === "yacc");
    toast(S.frontend === "yacc" ? "Using the lex & yacc (LALR) parser" : "Using the hand-written recursive-descent parser");
    compileNow();
  };
  $("#stdinBox").oninput = (e) => { if (S.active) store.set("stdin:" + S.active, e.target.value); };
  $("#stdinBox").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } };
  $("#aiAuto").onchange = (e) => store.set("aiAuto", e.target.checked);
  $("#modelSelect").onchange = (e) => { store.set("model", e.target.value); checkAi(); };
  $("#modelSelect").onfocus = () => { if (!S.ai.ok) checkAi(true); };
  $("#aiFix").onclick = () => runAi("fix");
  $("#aiBugs").onclick = () => runAi("bugs");
  $("#aiRefactor").onclick = () => runAi("refactor");
  $("#aiFlow").onclick = () => runAi("flow");
  $("#aiOpt").onclick = () => runAi("optimize");
  $("#aiReferee").onclick = () => runReferee();
  $("#btnGuide").onclick = () => openGuide();
  $("#aiDocs").onclick = () => {
    const r = S.compile;
    if (r && !r.success) runAi("docs", { title: "Your code, with each error explained" });
    else runAi("docs");
  };
  $("#aiFeed").onclick = (e) => {
    const w = e.target.closest(".where"); if (w) { gotoLine(+w.dataset.line); return; }
    const b = e.target.closest("[data-act]"); if (b) cardAction(b.closest(".card"), b.dataset.act, b);
  };
  $("#diffAccept").onclick = acceptDiff;
  $("#diffReject").onclick = closeDiff;
  $("#toggleAI").onclick = () => toggleAiPanel();
  $$(".act[data-side]").forEach((b) => b.onclick = () => {
    $$(".act[data-side]").forEach((x) => x.classList.toggle("active", x === b));
    $$(".side-view").forEach((v) => v.hidden = v.id !== "side-" + b.dataset.side);
  });
  $("#fileList").onclick = (e) => { const li = e.target.closest("li[data-name]"); if (li) openFile(li.dataset.name).catch((er) => toast(er.message)); };
  $("#outline").onclick = (e) => { const li = e.target.closest("li[data-line]"); if (li) gotoLine(+li.dataset.line); };
  $("#tabs").onclick = (e) => {
    const x = e.target.closest("[data-close]"); if (x) { e.stopPropagation(); closeTab(x.dataset.close); return; }
    const t = e.target.closest(".tab"); if (t && t.dataset.name !== S.active) activate(t.dataset.name);
  };
  $("#tabs").onauxclick = (e) => { const t = e.target.closest(".tab"); if (t && e.button === 1) closeTab(t.dataset.name); };
  $("#btnNewFile").onclick = () => { $("#newFileForm").hidden = false; $("#newFileName").value = ""; $("#newFileName").focus(); };
  $("#btnRefresh").onclick = () => refreshFiles();
  $("#newFileForm").onsubmit = async (e) => {
    e.preventDefault(); const n = $("#newFileName").value.trim(); if (!n) return;
    $("#newFileForm").hidden = true;
    try { await newFile(n, "int main() {\n    \n    return 0;\n}\n"); editor.setPosition({ lineNumber: 2, column: 5 }); }
    catch (er) { toast(er.message); }
  };
  $("#newFileName").onkeydown = (e) => { if (e.key === "Escape") $("#newFileForm").hidden = true; };
  $("#newFileName").onblur = () => setTimeout(() => { $("#newFileForm").hidden = true; }, 150);
  $$(".panel-tabs > button[data-view]").forEach((b) => b.onclick = () => showView(b.dataset.view));
  $$("#rail li").forEach((li) => $("button", li).onclick = () => showView(li.dataset.view));
  $("#btnClearTerm").onclick = () => { $("#terminal").innerHTML = ""; };
  $("#problems").onclick = (e) => { const li = e.target.closest("li[data-i]"); if (li) { const d = S.compile.diagnostics[+li.dataset.i]; gotoLine(d.line, d.column); } };
  $("#sbProblems").onclick = () => showView("problems");
  $("#tokens").onclick = (e) => {
    const el = e.target.closest(".tok"); if (!el) return;
    const k = S.compile.tokens[+el.dataset.i];
    editor.setSelection(new monaco.Range(k.line, k.column, k.line, k.column + k.value.length)); editor.revealLineInCenterIfOutsideViewport(k.line); editor.focus();
  };
  $("#ast").onclick = (e) => { const ln = e.target.closest(".ln"); if (ln) { e.preventDefault(); gotoLine(+ln.dataset.line); } };
  $("#ir").onclick = (e) => { const li = e.target.closest("li[data-line]"); if (li) gotoLine(+li.dataset.line, 1, false); };
  $("#ir").onmouseover = (e) => {
    const li = e.target.closest("li[data-line]");
    deco.irsrc.set(li ? [{ range: new monaco.Range(+li.dataset.line, 1, +li.dataset.line, 1), options: { isWholeLine: true, className: "ir-src-line" } }] : []);
  };
  $("#ir").onmouseleave = () => deco.irsrc.clear();
  $("#calls").onclick = (e) => { const g = e.target.closest("[data-line]"); if (g) gotoLine(+g.dataset.line); };
  $$(".subtabs button").forEach((b) => b.onclick = () => { S.targetMode = b.dataset.target; renderTarget(); });
  $("#debugBar").onclick = (e) => { const b = e.target.closest("[data-dbg]"); if (b) dbg(b.dataset.dbg); };
  $("#vars").innerHTML = '<div class="empty-view">Start the debugger (F9) to see variables change step by step. Click in the margin left of a line number to set a breakpoint.</div>';

  // resizable bottom panel
  const rz = $("#resizer"), panel = $("#panel");
  rz.onmousedown = (e) => {
    e.preventDefault(); rz.classList.add("drag");
    const y0 = e.clientY, h0 = panel.offsetHeight;
    const move = (ev) => { panel.style.height = Math.max(90, Math.min(window.innerHeight - 220, h0 - (ev.clientY - y0))) + "px"; };
    const up = () => { rz.classList.remove("drag"); store.set("panelHeight", panel.offsetHeight); removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
  };

  // keyboard shortcuts (capture phase so they work while the editor has focus)
  addEventListener("keydown", (e) => {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    let handled = true;
    if (ctrl && k.toLowerCase() === "s") saveActive().then(() => toast(`Saved ${S.active}`)).catch((er) => toast(er.message));
    else if (ctrl && k.toLowerCase() === "b") compileClicked();
    else if (k === "F5" && e.shiftKey) stopDebug();
    else if (k === "F5") run();
    else if (k === "F9") startDebug().catch((er) => toast(er.message));
    else if (k === "F10" && S.debug) dbg(e.shiftKey ? "back" : "over");
    else if (k === "F11" && S.debug) dbg(e.shiftKey ? "out" : "line");
    else if (k === "F8" && S.debug) dbg("cont");
    else if (k === "Escape" && !$("#diffView").hidden) closeDiff();
    else handled = false;
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  addEventListener("beforeunload", (e) => { if (S.tabs.some(isDirty)) { e.preventDefault(); e.returnValue = ""; } });
}
