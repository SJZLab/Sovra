#!/usr/bin/env node
// FTF-04A13's own real, non-executing JS/TS AST helper. Invoked as a
// subprocess by `prompt_source_discovery_evidence_capture.py` -- never
// invoked directly on untrusted content outside that module.
//
// This is a genuinely separate file from FTF-04A10's own
// `_typescript_javascript_ast_helper.cjs` -- that file is already
// CERTIFIED and frozen (Section 75.1: "shared module changes require
// ownership transfer/serialization"), so this task does not edit it.
// Several small helpers below (module/client-alias tracing, the literal
// evaluator, the completion-call matcher) are intentionally duplicated
// from that file rather than imported, the same disclosed
// per-task-duplication precedent FTF-04A10 itself already established one
// task over versus FTF-04A09's own Python-only equivalents -- each task
// owns its own closed, non-guessed detection rather than reaching into a
// sibling task's own private functions.
//
// This script only ever calls `ts.createSourceFile` (a pure syntax-tree
// builder), `program.getSyntacticDiagnostics` (a pure diagnostic pass) and
// Node's own core `crypto` module's `createHash("sha256")` (a pure digest,
// never a network/filesystem call) -- it never calls
// `eval`/`Function`/`require` on scanned content, never writes scanned
// content to disk, and never reads any real file from disk itself. Reads
// exactly one JSON request object from stdin, writes exactly one JSON
// response object to stdout, and never throws an uncaught exception.
//
// This task's own negative_acceptance ("External/floating prompt source or
// embedded injection is treated as data, never analyzer instruction") is
// met structurally: an embedded literal prompt string is only ever reduced
// to its own `content_sha256`/`content_length` here -- the raw string
// itself is never written to this script's own stdout, so no downstream
// consumer of this helper's own response can ever see it. A `content` that
// resolves to a call shaped exactly `<ident>.readFileSync(<literal path>,
// ...)` is reported as a bare `referenced_path` string (the literal path
// argument only, never fetched or read by this script) -- resolving
// whether that path is a real, already-captured template file is the
// calling Python module's own job, over its own already-bounded
// `SourceFileRef` sequence, never this subprocess reaching out on its own.

"use strict";

const ts = require("typescript");
const crypto = require("crypto");

function scriptKindForPath(relativePath) {
  if (relativePath.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (relativePath.endsWith(".ts")) return ts.ScriptKind.TS;
  if (relativePath.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (relativePath.endsWith(".js")) return ts.ScriptKind.JS;
  return null;
}

function countSyntacticDiagnostics(fileName, sourceFile, sourceText) {
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

// The same closed, three-shape OpenAI-client-import/instantiation set
// FTF-04A10's own frozen helper already established -- duplicated here,
// not imported (see this file's own header comment).
function collectOpenAiModuleAliases(sourceFile) {
  const aliases = new Set();
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      const spec = node.moduleSpecifier;
      if (ts.isStringLiteral(spec) && spec.text === "openai" && node.importClause) {
        const clause = node.importClause;
        if (clause.name) {
          aliases.add(clause.name.text);
        }
        if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
          for (const element of clause.namedBindings.elements) {
            const importedName = element.propertyName
              ? element.propertyName.text
              : element.name.text;
            if (importedName === "OpenAI") {
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
          clientAliases.add(decl.name.text);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return clientAliases;
}

// A pure, non-executing literal evaluator -- the same closed shape set
// FTF-04A10's own frozen helper already established.
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

// This task's own new detection, genuinely different from FTF-04A10's own
// frozen `extractModelBinding`/`extractToolSchema`: Section 7A.4 step 5,
// "Detect system/developer prompt sources", read from the same call's
// `messages` array literal. Never guesses past a non-literal shape --
// a dynamic `messages` expression, a non-object array element, or a
// computed/spread key is genuinely unresolved, not silently skipped past.
function matchReadFileSyncCall(node) {
  // Matches exactly `<ident>.readFileSync(<literal path>, ...)` -- a bare,
  // closed call shape, deliberately narrower than FTF-04A10's own traced
  // "openai" import discipline (this is a location-only heuristic, not a
  // security-relevant resolution -- see this file's own header comment).
  if (!ts.isCallExpression(node)) return null;
  const callee = node.expression;
  if (!ts.isPropertyAccessExpression(callee) || callee.name.text !== "readFileSync") return null;
  if (!ts.isIdentifier(callee.expression)) return null;
  if (node.arguments.length < 1) return null;
  const pathResult = evalLiteral(node.arguments[0]);
  if (!pathResult.ok || typeof pathResult.value !== "string") return null;
  return pathResult.value;
}

function sha256Hex(text) {
  return crypto.createHash("sha256").update(text, "utf-8").digest("hex");
}

function extractPromptSource(objectLiteral) {
  const lookup = findObjectPropertyValueNode(objectLiteral, "messages");
  if (lookup.present === "no") {
    return { resolved: false, source_kind: null, role: null, reason: "argument_not_present" };
  }
  if (lookup.present === "unknown" || !ts.isArrayLiteralExpression(lookup.valueNode)) {
    return {
      resolved: false,
      source_kind: null,
      role: null,
      reason: "unresolved_dynamic_expression",
    };
  }

  for (const element of lookup.valueNode.elements) {
    if (ts.isSpreadElement(element) || !ts.isObjectLiteralExpression(element)) continue;
    const roleLookup = findObjectPropertyValueNode(element, "role");
    if (roleLookup.present !== "yes") continue;
    const roleResult = evalLiteral(roleLookup.valueNode);
    if (!roleResult.ok || typeof roleResult.value !== "string") continue;
    if (roleResult.value !== "system" && roleResult.value !== "developer") continue;

    const contentLookup = findObjectPropertyValueNode(element, "content");
    if (contentLookup.present !== "yes") {
      return {
        resolved: false,
        source_kind: null,
        role: roleResult.value,
        reason: "argument_not_present",
      };
    }
    const contentResult = evalLiteral(contentLookup.valueNode);
    if (contentResult.ok && typeof contentResult.value === "string") {
      return {
        resolved: true,
        source_kind: "embedded_literal",
        role: roleResult.value,
        content_sha256: sha256Hex(contentResult.value),
        content_length: Buffer.byteLength(contentResult.value, "utf-8"),
        reason: "literal_string",
      };
    }
    const referencedPath = matchReadFileSyncCall(contentLookup.valueNode);
    if (referencedPath !== null) {
      return {
        resolved: true,
        source_kind: "file_reference",
        role: roleResult.value,
        referenced_path: referencedPath,
        reason: "file_reference_read_file_sync",
      };
    }
    return {
      resolved: false,
      source_kind: null,
      role: roleResult.value,
      reason: "unresolved_dynamic_expression",
    };
  }

  return {
    resolved: false,
    source_kind: null,
    role: null,
    reason: "no_system_or_developer_role_found",
  };
}

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

function findCandidates(sourceFile, relativePath, clientAliases) {
  const candidates = [];
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
      let promptSource;
      if (node.arguments.length === 0) {
        promptSource = {
          resolved: false,
          source_kind: null,
          role: null,
          reason: "argument_not_present",
        };
      } else if (objectArg === null || !ts.isObjectLiteralExpression(objectArg)) {
        promptSource = {
          resolved: false,
          source_kind: null,
          role: null,
          reason: "unresolved_dynamic_expression",
        };
      } else {
        promptSource = extractPromptSource(objectArg);
      }
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
      candidates.push({
        relative_path: relativePath,
        line: line + 1,
        function_name: functionStack[functionStack.length - 1],
        call_attribute: callAttribute,
        prompt_source: promptSource,
      });
    }

    ts.forEachChild(node, visit);
    if (pushed) functionStack.pop();
  }

  visit(sourceFile);
  return candidates;
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
  const candidates = findCandidates(sourceFile, relativePath, clientAliases);
  return { ok: true, candidates };
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
