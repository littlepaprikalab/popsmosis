import type { MetadataRoute } from 'next';

// What Android uses for "Add to Home screen". The icons come from scripts/icons.mjs.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'POPsmosis — droplets for learning',
    short_name: 'POPsmosis',
    description: 'Droplets for learning. Ask for any topic and get a droplet: a short lesson with a narrated video and a song.',
    start_url: '/',
    display: 'browser',
    background_color: '#111318',
    theme_color: '#0B7499',
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
