// Vite's `?raw` suffix imports a file's text (used to read the API source that
// defines a vocabulary the UI must mirror, so the two cannot drift silently).
declare module "*?raw" {
  const content: string;
  export default content;
}
