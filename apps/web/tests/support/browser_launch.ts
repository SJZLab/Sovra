// Launch options shared by the real-Chromium specs. Playwright pins the browser build to its own
// version; a machine that has a different Chromium installed (for example a sandbox with a newer
// build) sets PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to that binary. Unset, the default build is used,
// so CI (which runs `playwright install chromium`) behaves exactly as before.
export function browserLaunchOptions(): { executablePath?: string } {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  return executablePath ? { executablePath } : {};
}
