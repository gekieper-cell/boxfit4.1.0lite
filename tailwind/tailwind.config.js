// Genera api/static/css/tw.css con las clases que usan los templates (reemplaza el CDN de Tailwind)
module.exports = {
  content: ['./api/templates/**/*.html'],
  theme: { extend: {} },
  plugins: [],
};
