// Global ambient background for the whole app.
// Bright Desk retires the ambient wash entirely: no violet/cyan glow, no
// texture, nothing behind the content. This renders nothing on purpose.
// App.tsx still imports and mounts this component, so the default export
// stays in place.
export default function AmbientBackground() {
  return null
}
