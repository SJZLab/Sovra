#!/usr/bin/env node
// FTF-04A10's own real, non-executing JS/TS AST helper. Invoked as a
// subprocess by
// `typescript_javascript_deterministic_agent_config_tool_discovery.py` --
// never invoked directly on untrusted content outside that module. See
// that module's own docstring for the full scope boundary and the
// "isolated approved parser" resolution (this repo's own already-declared
// root `typescript` devDependency, never a new runtime dependency).
//
// This script only ever calls `ts.createSourceFile` (a pure syntax-tree
// builder) and `program.getSyntacticDiagnostics` (a pure diagnostic pass
// over that same tree) -- it never calls `eval`/`Function`/`require` on
// scanned content, never writes the scanned content to disk, and never
// imports/executes the file it is asked to parse. Reads exactly one
// JSON request object from stdin, writes exactly one JSON response
// object to stdout, and never throws an uncaught exception -- every
// failure path is caught and reported as `{"ok": false, ...}` so the
// calling Python worker can count it as a genuine parse error, the same
// discipline `ast.parse`'s own caught-exception set already established
// for the Python-only sibling worker (FTF-04A09).

"use strict";

const ts = require("typescript");

function scriptKindForPath(relativePath) {
  if (relativePath.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (relativePath.endsWith(".ts")) return ts.ScriptKind.TS;
  if (relativePath.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (relativePath.endsWith(".js")) return ts.ScriptKind.JS;
  return null;
}

function countSyntacticDiagnostics(fileName, sourceFile, sourceText) {
  // The only public, documented way to learn whether `ts.createSourceFile`
  // (which never throws on malformed input -- it is deliberately
  // error-tolerant) actually produced a genuinely broken tree is a real
  // `Program`'s own `getSyntacticDiagnostics`. This host never touches the
  // real filesystem -- it serves exactly the one already-parsed
  // `sourceFile` back to the program, and reports every other path
  // (default lib, directory listing) as absent.
  const host = {
    getSourceFile: (name) => (name === fileName ? sourceFile : undefined),
    getDefaultLibFileName: () => "lib.d.ts",
    writeFile: () => {},
    getCurrentDirectory: () => "",
    getDirectories: () => [],
    fileExists: (name) => name === fileName,
    readFile: (name) => (name === fileName ? sourceText : undefined),
    getCanonicalFileName: (name) => name,
    useCaseSensitiveFileNames: () => true,
    getNewLine: () => "\n",
  };
  const program = ts.createProgram(
    [fileName],
    { noResolve: true, allowJs: true, target: ts.ScriptTarget.Latest, noLib: true },
    host,
  );
  return program.getSyntacticDiagnostics(sourceFile).length;
}

// Section 7A.4's own closed, non-guessed import-shape set for the JS/TS
// ecosystem's real client for an OpenAI-compatible endpoint (this repo's
// own `/v1/chat/completions` LiteLLM-proxy contract -- see this task's own
// module docstring for the full reasoning). Exactly three shapes, the same
// count and the same "bare / aliased / destructured" structure FTF-04A09's
// own three Python import shapes already established -- never a namespace
// import (`import * as ns from "openai"`) or a dynamically-computed
// `require(expr)`, each a genuinely different, unresolved shape this
// module does not guess through.
function collectOpenAiModuleAliases(sourceFile) {
  const aliases = new Set();
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      const spec = node.moduleSpecifier;
      if (ts.isStringLiteral(spec) && spec.text === "openai" && node.importClause) {
        const clause = node.importClause;
        if (clause.name) {
          // import OpenAI from "openai"  (any local default-import name)
          aliases.add(clause.name.text);
        }
        if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
          for (const element of clause.namedBindings.elements) {
            const importedName = element.propertyName
              ? element.propertyName.text
              : element.name.text;
            if (importedName === "OpenAI") {
              // import { OpenAI [as X] } from "openai"
              aliases.add(element.name.text);
            }
          }
        }
      }
    } else if (ts.isVariableStatement(node)) {
      for (const decl of node.declarationList.declarations) {
        if (
          ts.isIdentifier(decl.name) &&
          decl.initializer &&
          ts.isCallExpression(decl.initializer) &&
          ts.isIdentifier(decl.initializer.expression) &&
          decl.initializer.expression.text === "require" &&
          decl.initializer.arguments.length === 1 &&
          ts.isStringLiteral(decl.initializer.arguments[0]) &&
          decl.initializer.arguments[0].text === "openai"
        ) {
          // const X = require("openai")
          aliases.add(decl.name.text);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return aliases;
}

function collectClientAliases(sourceFile, moduleAliases) {
  const clientAliases = new Set();
  function visit(node) {
    if (ts.isVariableStatement(node)) {
      for (const decl of node.declarationList.declarations) {
        if (
          ts.isIdentifier(decl.name) &&
          decl.initializer &&
          ts.isNewExpression(decl.initializer) &&
          ts.isIdentifier(decl.initializer.expression) &&
          moduleAliases.has(decl.initializer.expression.text)
        ) {
          // const client = new <moduleAlias>(...)
          clientAliases.add(decl.name.text);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return clientAliases;
}

// A pure, non-executing literal evaluator -- the direct JS/TS analog of
// `ast.literal_eval` (see FTF-04A09's own module docstring): resolves only
// a closed set of literal syntax shapes, never an identifier, call,
// template-literal-with-expressions, or spread -- each of those is
// genuinely unresolved, never guessed.
function evalLiteral(node) {
  if (ts.isStringLiteralLike(node)) return { ok: true, value: node.text };
  if (ts.isNumericLiteral(node)) return { ok: true, value: Number(node.text) };
  if (node.kind === ts.SyntaxKind.TrueKeyword) return { ok: true, value: true };
  if (node.kind === ts.SyntaxKind.FalseKeyword) return { ok: true, value: false };
  if (node.kind === ts.SyntaxKind.NullKeyword) return { ok: true, value: null };
  if (ts.isArrayLiteralExpression(node)) {
    const values = [];
    for (const element of node.elements) {
      if (ts.isSpreadElement(element)) return { ok: false };
      const result = evalLiteral(element);
      if (!result.ok) return { ok: false };
      values.push(result.value);
    }
    return { ok: true, value: values };
  }
  if (ts.isObjectLiteralExpression(node)) {
    const obj = {};
    for (const prop of node.properties) {
      if (!ts.isPropertyAssignment(prop)) return { ok: false };
      let key;
      if (ts.isIdentifier(prop.name) || ts.isStringLiteralLike(prop.name)) {
        key = prop.name.text;
      } else {
        return { ok: false };
      }
      const result = evalLiteral(prop.initializer);
      if (!result.ok) return { ok: false };
      obj[key] = result.value;
    }
    return { ok: true, value: obj };
  }
  return { ok: false };
}

// Finds a named property's *value node* inside a real (non-spread) object
// literal's own top-level members, honestly distinguishing "the property
// is genuinely absent" from "the property's value could not itself be
// resolved" -- the same distinction FTF-04A09's own `_keyword_value`
// draws for a Python keyword argument.
function findObjectPropertyValueNode(objectLiteral, propertyName) {
  for (const prop of objectLiteral.properties) {
    if (ts.isSpreadAssignment(prop)) return { present: "unknown" };
    if (ts.isPropertyAssignment(prop) || ts.isShorthandPropertyAssignment(prop)) {
      const nameNode = prop.name;
      let key = null;
      if (ts.isIdentifier(nameNode) || ts.isStringLiteralLike(nameNode)) {
        key = nameNode.text;
      }
      if (key === propertyName) {
        const valueNode = ts.isShorthandPropertyAssignment(prop) ? prop.name : prop.initializer;
        return { present: "yes", valueNode };
      }
    }
  }
  return { present: "no" };
}

function extractModelBinding(objectLiteral) {
  const lookup = findObjectPropertyValueNode(objectLiteral, "model");
  if (lookup.present === "no") {
    return { resolved: false, model_alias: null, reason: "argument_not_present" };
  }
  if (lookup.present === "unknown") {
    return { resolved: false, model_alias: null, reason: "unresolved_dynamic_expression" };
  }
  const result = evalLiteral(lookup.valueNode);
  if (!result.ok || typeof result.value !== "string") {
    return { resolved: false, model_alias: null, reason: "unresolved_dynamic_expression" };
  }
  return { resolved: true, model_alias: result.value, reason: "literal_string" };
}

function openAiFunctionToolName(entry) {
  if (entry === null || typeof entry !== "object" || Array.isArray(entry)) return null;
  if (entry.type !== "function") return null;
  const fn = entry.function;
  if (fn === null || typeof fn !== "object" || Array.isArray(fn)) return null;
  return typeof fn.name === "string" ? fn.name : null;
}

function extractToolSchema(objectLiteral) {
  const lookup = findObjectPropertyValueNode(objectLiteral, "tools");
  if (lookup.present === "no") {
    return { resolved: false, tool_names: [], reason: "argument_not_present" };
  }
  if (lookup.present === "unknown") {
    return { resolved: false, tool_names: [], reason: "unresolved_dynamic_expression" };
  }
  const result = evalLiteral(lookup.valueNode);
  if (!result.ok || !Array.isArray(result.value)) {
    return { resolved: false, tool_names: [], reason: "unresolved_dynamic_expression" };
  }
  const toolNames = [];
  for (const entry of result.value) {
    const name = openAiFunctionToolName(entry);
    if (name === null) {
      return { resolved: false, tool_names: [], reason: "unresolved_dynamic_expression" };
    }
    toolNames.push(name);
  }
  return { resolved: true, tool_names: toolNames, reason: "literal_openai_function_list" };
}

// Matches exactly `<clientAlias>.chat.completions.create(...)` -- a plain
// Identifier client reference through a fixed three-level property chain,
// never a computed or otherwise-shaped callee. `await <call>` needs no
// special handling: the AwaitExpression merely wraps this same
// CallExpression node, which the generic tree walk still visits directly.
function matchCompletionCall(node, clientAliases) {
  if (!ts.isCallExpression(node)) return null;
  const createAccess = node.expression;
  if (!ts.isPropertyAccessExpression(createAccess) || createAccess.name.text !== "create") {
    return null;
  }
  const completionsAccess = createAccess.expression;
  if (
    !ts.isPropertyAccessExpression(completionsAccess) ||
    completionsAccess.name.text !== "completions"
  ) {
    return null;
  }
  const chatAccess = completionsAccess.expression;
  if (!ts.isPropertyAccessExpression(chatAccess) || chatAccess.name.text !== "chat") {
    return null;
  }
  const clientExpr = chatAccess.expression;
  if (!ts.isIdentifier(clientExpr) || !clientAliases.has(clientExpr.text)) {
    return null;
  }
  return `${clientExpr.text}.chat.completions.create`;
}

function functionScopeName(node) {
  if (node.name && (ts.isIdentifier(node.name) || ts.isStringLiteralLike(node.name))) {
    return node.name.text;
  }
  const parent = node.parent;
  if (parent) {
    if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
      return parent.name.text;
    }
    if (
      (ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent)) &&
      parent.name &&
      (ts.isIdentifier(parent.name) || ts.isStringLiteralLike(parent.name))
    ) {
      return parent.name.text;
    }
  }
  return "<anonymous>";
}

function findEntrypoints(sourceFile, relativePath, clientAliases) {
  const entrypoints = [];
  const functionStack = ["<module>"];

  function visit(node) {
    let pushed = false;
    if (
      ts.isFunctionDeclaration(node) ||
      ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node) ||
      ts.isMethodDeclaration(node)
    ) {
      functionStack.push(functionScopeName(node));
      pushed = true;
    }

    const callAttribute = matchCompletionCall(node, clientAliases);
    if (callAttribute !== null) {
      const objectArg = node.arguments.length === 1 ? node.arguments[0] : null;
      let modelBinding;
      let toolSchema;
      if (node.arguments.length === 0) {
        modelBinding = { resolved: false, model_alias: null, reason: "argument_not_present" };
        toolSchema = { resolved: false, tool_names: [], reason: "argument_not_present" };
      } else if (objectArg === null || !ts.isObjectLiteralExpression(objectArg)) {
        modelBinding = {
          resolved: false,
          model_alias: null,
          reason: "unresolved_dynamic_expression",
        };
        toolSchema = { resolved: false, tool_names: [], reason: "unresolved_dynamic_expression" };
      } else {
        modelBinding = extractModelBinding(objectArg);
        toolSchema = extractToolSchema(objectArg);
      }
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
      entrypoints.push({
        relative_path: relativePath,
        line: line + 1,
        function_name: functionStack[functionStack.length - 1],
        call_attribute: callAttribute,
        model_binding: modelBinding,
        tool_schema: toolSchema,
      });
    }

    ts.forEachChild(node, visit);
    if (pushed) functionStack.pop();
  }

  visit(sourceFile);
  return entrypoints;
}

function analyze(relativePath, sourceText) {
  const scriptKind = scriptKindForPath(relativePath);
  if (scriptKind === null) {
    return { ok: false, reason: "unsupported_extension" };
  }
  let sourceFile;
  try {
    sourceFile = ts.createSourceFile(
      relativePath,
      sourceText,
      ts.ScriptTarget.Latest,
      true,
      scriptKind,
    );
  } catch {
    return { ok: false, reason: "parse_error" };
  }

  let diagnosticCount;
  try {
    diagnosticCount = countSyntacticDiagnostics(relativePath, sourceFile, sourceText);
  } catch {
    return { ok: false, reason: "parse_error" };
  }
  if (diagnosticCount > 0) {
    return { ok: false, reason: "parse_error" };
  }

  const moduleAliases = collectOpenAiModuleAliases(sourceFile);
  const clientAliases = collectClientAliases(sourceFile, moduleAliases);
  const entrypoints = findEntrypoints(sourceFile, relativePath, clientAliases);
  return { ok: true, entrypoints };
}

function main() {
  const chunks = [];
  process.stdin.on("data", (chunk) => chunks.push(chunk));
  process.stdin.on("end", () => {
    let response;
    try {
      const request = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
      response = analyze(request.relative_path, request.source_text);
    } catch {
      response = { ok: false, reason: "parse_error" };
    }
    process.stdout.write(JSON.stringify(response));
  });
}

main();
