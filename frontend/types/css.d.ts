// Allow TypeScript to accept CSS file imports (e.g. maplibre-gl/dist/maplibre-gl.css)
declare module '*.css' {
  const content: string;
  export default content;
}
