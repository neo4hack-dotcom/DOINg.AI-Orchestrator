import { memo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

/* Remote images in model output would be fetched by the browser without a click - a way to leak data
   through the URL (and the Content-Security-Policy blocks them anyway). They are shown as links instead. */
function SafeImage({ src, alt }: { src?: string | Blob; alt?: string }) {
  const url = typeof src === 'string' ? src : '';
  if (/^(data:image\/|blob:|\/)/i.test(url) && !url.startsWith('//')) return <img src={url} alt={alt || ''} className="max-w-full rounded" />;
  return (
    <span className="rounded border border-line px-1 text-[11px] text-faint" title={`Remote image not loaded: ${url}`}>
      [image{alt ? `: ${alt}` : ''} - not loaded]
    </span>
  );
}

const components: Components = {
  img: ({ src, alt }) => <SafeImage src={src} alt={alt} />,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
});
