import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Schlanker Markdown-Renderer für Pläne/Beschreibungen (ohne Typography-Plugin,
// Styling über Arbitrary-Variants). GFM aktiv → Tabellen, Task-Listen, ~~Strike~~.
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="text-sm leading-relaxed
      [&_h1]:mb-1 [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-semibold
      [&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:font-semibold
      [&_h3]:mt-2 [&_h3]:font-medium
      [&_p]:my-1.5
      [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5
      [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5
      [&_li]:my-0.5
      [&_code]:rounded [&_code]:bg-surface [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs
      [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-surface [&_pre]:p-2
      [&_pre_code]:bg-transparent [&_pre_code]:p-0
      [&_a]:text-brand [&_a]:underline
      [&_strong]:font-semibold
      [&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-muted
      [&_table]:my-2 [&_table]:block [&_table]:w-max [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:border-collapse
      [&_th]:border [&_th]:border-line [&_th]:bg-surface [&_th]:px-2 [&_th]:py-1 [&_th]:text-left
      [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1
      [&_hr]:my-3 [&_hr]:border-line">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
