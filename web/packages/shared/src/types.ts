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
  | { type: "leave_confirmation_required"; request: LeaveRequest }
  | { type: "leave_approval_required"; request_id: string }
  | { type: "leave_submitted"; request_id: string }
  | { type: "leave_cancelled"; request_id: string }
  | { type: "leave_workflow_error"; content: string }
  | { type: string; [key: string]: unknown };

export interface LeaveType { id: string; code: string; name: string; is_active: boolean; allow_half_days: boolean; }
export interface LeaveBalance { id: string; leave_type_id: string; leave_type_code: string; leave_type_name: string; year: number; entitled_days: number; reserved_days: number; used_days: number; remaining_days: number; version: number; }
export interface LeaveRequest {
  id: string; chat_thread_id: string; leave_type_id: string; leave_type_code: string; leave_type_name: string;
  start_date: string; end_date: string; start_period: "am" | "pm"; end_period: "am" | "pm";
  duration_days: number; reason: string; status: string; cancel_reason: string | null; draft_expires_at: string | null;
  workflow_stage: string; resume_status: string; version: number; created_at: string; updated_at: string; balance: LeaveBalance | null;
}
export interface ApprovalTask { id: string; leave_request: LeaveRequest; status: string; requester_username: string; requester_email: string; decision_comment: string | null; decided_at: string | null; version: number; created_at: string; }
export interface ApprovalTaskPage { items: ApprovalTask[]; total: number; page: number; page_size: number; }
export interface LeaveTransition { request: LeaveRequest; workflow_resume: "waiting" | "completed" | "resume_pending"; events: Array<{ type: "leave_submitted" | "leave_cancelled" | "leave_workflow_error"; request_id?: string; content?: string }>; }
export interface AppNotification { id: string; category: string; entity_type: string; entity_id: string; title: string; body: string; read_at: string | null; created_at: string; }
export interface NotificationPage { items: AppNotification[]; unread: number; }

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
  redis: string;
  traffic_governance: string;
}

export interface AdminUser extends UserProfile {
  thread_count: number;
}

export interface PersonnelProfile {
  user_id: string;
  full_name: string;
  employee_no: string;
  department: string;
  job_title: string;
  work_email: string | null;
  work_phone: string | null;
  employment_status: "active" | "inactive";
}

export interface PersonnelProfileDetail {
  profile: PersonnelProfile | null;
  can_query_personnel: boolean;
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
