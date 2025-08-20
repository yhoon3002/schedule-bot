import axiosInstance from "../utils/axiosInstance";
import { getSessionId } from "../utils/session";

export interface ChatHistoryItem {
    role: "user" | "assistant" | "system";
    content: string;
}

export interface ChatRequest {
    user_message: string;
    history?: ChatHistoryItem[];
    session_id?: string;
}

export interface ChatResponse {
    reply: string;
    tool_result?: Record<string, unknown> | Record<string, unknown>[] | null;
}

export async function chatSchedule(
    payload: ChatRequest
): Promise<ChatResponse> {
    const session_id = getSessionId();

    const { data } = await axiosInstance.post<ChatResponse>("/schedules/chat", {
        ...payload,
        session_id,
    });

    return data;
}
