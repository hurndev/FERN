/// <reference types="vite/client" />

declare module '*.module.css' {
  const classes: { readonly [key: string]: string }
  export default classes
}

interface Window {
  jdenticon?: {
    update: (el: SVGElement | HTMLElement) => void
  }
}
