#!/usr/bin/env node
// FTF-04A14's own real, non-executing JS/TS AST helper. Invoked as a
// subprocess by `tool_schema_permission_discovery_evidence_capture.py` --
// never invoked directly on untrusted content outside that module.
//
// This is a genuinely separate file from FTF-04A10's own
// `_typescript_javascript_ast_helper.cjs` and FTF-04A13's own
// `_typescript_javascript_prompt_source_ast_helper.cjs` -- both already
// CERTIFIED and frozen (Section 75.1: "shared module changes require
// ownership transfer/serialization"), so this task does not edit either.
// Several small helpers below (module/client-alias tracing, the literal
// evaluator, the completion-call matcher) are intentionally duplicated
// from those files rather than imported, the same disclosed
// per-task-duplication precedent FTF-04A10/FTF-04A13 already established.
//
// This script only ever calls `ts.createSourceFile` (a pure syntax-tree
// builder) and `program.getSyntacticDiagnostics` (a pure diagnostic pass)
// -- it never calls `eval`/`Function`/`require` on scanned content, never
// writes scanned content to disk, and never reads any real file from disk
// itself. Reads exactly one JSON request object from stdin, writes exactly
// one JSON response object to stdout, and never throws an uncaught
// exception.
//
// This script never reads a `description` property for any purpose --
// this task's own negative_acceptance ("Unknown authorization is not
// inferred from tool description") is met structurally here: neither
// `extractToolDefinitionFields` nor `extractResponseFormat` below ever
// looks up a `description` key, and `side_effect_class`/
// `requires_confirmation` are resolved only from their own same-named
// object-literal property. This script also never itself decides whether
// a resolved `inputSchema`/`outputSchema`/`sideEffectClass` value is
// *valid* -- it reports only the real literal value (or the fact that the
// property is absent/non-literal/dynamically-computed) back to the
// calling Python module, which alone owns JSON Schema/enum validation by
// reusing FTF-0405's/FTF-0406's own already-CERTIFIED services (see that
// module's own docstring, "Reuse, not reimplementation").

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
// not imported (see this file's own header comment). Needed to detect
// this task's own step-7 `response_format` candidate on the same real
// `<client>.chat.completions.create(...)` call site.
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
// FTF-04A10's/FTF-04A13's own frozen helpers already established.
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
// is genuinely absent" from "the property's own value could not itself be
// resolved" from "a spread makes the whole shape statically unknown" --
// the same three-way distinction FTF-04A09's own `_keyword_value`/
// FTF-04A13's own `findObjectPropertyValueNode` already draw.
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

// This task's own real status shape, sent back to the calling Python
// module for it alone to interpret (see this file's own header comment,
// "Reuse, not reimplementation"): `"no"` (genuinely absent), `"unknown"`
// (a spread makes the shape statically unresolvable),
// `"non_literal"` (present, but not a literal this script can evaluate),
// or `"literal"` with the real evaluated `value`.
function fieldStatus(objectLiteral, propertyName) {
  const lookup = findObjectPropertyValueNode(objectLiteral, propertyName);
  if (lookup.present === "no") return { present: "no" };
  if (lookup.present === "unknown") return { present: "unknown" };
  const result = evalLiteral(lookup.valueNode);
  if (!result.ok) return { present: "non_literal" };
  return { present: "literal", value: result.value };
}

// Closed, disclosed, non-guessed function-call vocabulary for a
// standalone JS/TS tool definition -- the direct JS/TS analog of this
// task's own Python `@tool`/`@function_tool` decorator vocabulary (no
// decorator equivalent exists in JS/TS, so a bare call expression is the
// closest real, common convention -- e.g. the Vercel AI SDK's own
// `tool({...})` helper). See the calling Python module's own docstring,
// point 1, for the full disclosed reasoning.
const TOOL_DEFINITION_FUNCTION_NAMES = new Set(["tool", "defineTool"]);

function matchToolDefinitionCall(node) {
  if (!ts.isCallExpression(node)) return null;
  const callee = node.expression;
  if (ts.isIdentifier(callee) && TOOL_DEFINITION_FUNCTION_NAMES.has(callee.text)) {
    return callee.text;
  }
  return null;
}

// This task's own new field extraction -- genuinely different from
// FTF-04A10's own frozen `extractModelBinding`/`extractToolSchema` and
// FTF-04A13's own frozen `extractPromptSource`. Deliberately never reads
// `description` -- see this file's own header comment.
function extractToolDefinitionFields(objectLiteral) {
  const nameStatus = fieldStatus(objectLiteral, "name");
  const toolName =
    nameStatus.present === "literal" && typeof nameStatus.value === "string"
      ? nameStatus.value
      : null;
  return {
    tool_name: toolName,
    input_schema: fieldStatus(objectLiteral, "inputSchema"),
    output_schema: fieldStatus(objectLiteral, "outputSchema"),
    side_effect_class: fieldStatus(objectLiteral, "sideEffectClass"),
    requires_confirmation: fieldStatus(objectLiteral, "requiresConfirmation"),
  };
}

const NOT_APPLICABLE = { present: "no" };

function extractResponseFormatFields(objectLiteral) {
  return {
    tool_name: null,
    input_schema: NOT_APPLICABLE,
    output_schema: fieldStatus(objectLiteral, "response_format"),
    side_effect_class: NOT_APPLICABLE,
    requires_confirmation: NOT_APPLICABLE,
  };
}

// Matches exactly `<clientAlias>.chat.completions.create(...)` -- the
// same closed shape FTF-04A10's/FTF-04A13's own frozen helpers already
// established.
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

const EMPTY_FIELDS = {
  tool_name: null,
  input_schema: { present: "no" },
  output_schema: { present: "no" },
  side_effect_class: { present: "no" },
  requires_confirmation: { present: "no" },
};

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

    const toolDefinitionCallee = matchToolDefinitionCall(node);
    if (toolDefinitionCallee !== null) {
      const objectArg = node.arguments.length === 1 ? node.arguments[0] : null;
      const fields =
        objectArg !== null && ts.isObjectLiteralExpression(objectArg)
          ? extractToolDefinitionFields(objectArg)
          : EMPTY_FIELDS;
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
      candidates.push({
        relative_path: relativePath,
        line: line + 1,
        function_name: toolDefinitionCallee,
        reason: "tool_definition_call",
        ...fields,
      });
    }

    const completionCallAttribute = matchCompletionCall(node, clientAliases);
    if (completionCallAttribute !== null) {
      const objectArg = node.arguments.length === 1 ? node.arguments[0] : null;
      if (objectArg !== null && ts.isObjectLiteralExpression(objectArg)) {
        const responseFormatStatus = fieldStatus(objectArg, "response_format");
        // Only produced as a candidate when `response_format` is actually
        // present at this real call site -- see the calling Python
        // module's own docstring, step 7.
        if (responseFormatStatus.present !== "no") {
          const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
          candidates.push({
            relative_path: relativePath,
            line: line + 1,
            function_name: functionStack[functionStack.length - 1],
            reason: "structured_output_response_format",
            ...extractResponseFormatFields(objectArg),
          });
        }
      }
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
