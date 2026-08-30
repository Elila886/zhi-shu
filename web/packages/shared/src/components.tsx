import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";

export function BrandMark({ admin = false }: { admin?: boolean }) {
  return (
    <div className="brand-mark" aria-label={admin ? "知枢管理后台" : "知枢"}>
      <span className="brand-symbol" aria-hidden="true">{admin ? "◆" : "✦"}</span>
      <span>
        <strong>知枢</strong>
        <small>{admin ? "ADMIN CONSOLE" : "企业智能知识助手"}</small>
      </span>
    </div>
  );
}

export function Button({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`button ${className}`.trim()} {...props} />;
}

export function Spinner({ label = "加载中" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function EmptyState({ icon, children }: PropsWithChildren<{ icon?: ReactNode }>) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <p>{children}</p>
    </div>
  );
}
