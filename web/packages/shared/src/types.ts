import type { components } from "./generated/openapi";

type Schemas = components["schemas"];

export type Role = "user" | "admin" | "super_admin";

export type AuthUser = Pick<Schemas["UserPublic"], "id" | "email" | "username"> & { role: Role };

export type UserProfile = Omit<Schemas["UserPublic"], "role"> & { role: Role };

export type LoginResponse = Omit<Schemas["LoginResponse"], "user"> & { user: AuthUser };

export interface PublicConfig {
  model_names: string[];
  document_extensions: string[];
}

export type ChatThread = Schemas["ThreadPublic"];

export interface ChatMessage {
  role: "human" | "ai" | string;
  content: string;
}

export type ThreadDocument = Schemas["DocumentPublic"];

export type StreamEvent =
  | { type: "llm_chunk"; content: string }
  | { type: "tool_call"; name: string; args: unknown }
  | { type: "tool_result"; name: string; content: string }
  | { type: string; [key: string]: unknown };

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminOverview {
  users: number;
  active_users_30d: number;
  threads: number;
  documents: number;
  today_threads: number;
  failed_documents: number;
  generated_at: string;
}

export interface AdminHealth {
  backend: string;
  database: string;
  pgvector: string;
}

export interface AdminUser extends UserProfile {
  thread_count: number;
}

export interface AdminDocument {
  id: string;
  file_name: string;
  status: string;
  chunk_count: number;
  error_message: string | null;
  uploaded_at: string;
  thread_id: string;
  user_id: string;
  username: string;
  email: string;
}

export interface AuditLog {
  id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string | null;
  before_data: Record<string, unknown> | null;
  after_data: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}
