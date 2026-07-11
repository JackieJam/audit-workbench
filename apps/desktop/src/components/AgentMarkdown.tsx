import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = { content: string };

export function AgentMarkdown({ content }: Props) {
  return (
    <div className="agent-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
