// src/store/events.ts
import { defineStore } from "pinia";
import axiosInstance from "../utils/axiosInstance";
import { getSessionId } from "../utils/session";

type FCEvent = {
    id: string;
    title: string;
    start: string;
    end?: string | null;
    extendedProps?: Record<string, any>;
};

export const useEventsStore = defineStore("events", {
    state: () => ({
        items: [] as FCEvent[],
    }),
    getters: {
        asCalendarEvents: (s) => s.items,
    },
    actions: {
        async loadRange(timeMin: string, timeMax: string) {
            const session_id = getSessionId();
            const { data } = await axiosInstance.get(
                "/google/calendar/events",
                {
                    params: { session_id, timeMin, timeMax },
                }
            );
            this.items = (data.items || []).map((e: any) => ({
                id: e.id,
                title: e.title,
                start: e.start,
                end: e.end ?? null,
                extendedProps: {
                    description: e.description,
                    location: e.location,
                    attendees: e.attendees,
                },
            }));
        },

        async create(payload: {
            title: string;
            start: string;
            end: string;
            description?: string;
            location?: string;
            attendees?: string[];
        }) {
            const session_id = getSessionId();
            await axiosInstance.post("/google/calendar/events", {
                session_id,
                summary: payload.title, // 서버가 summary를 title로 매핑
                start: payload.start,
                end: payload.end,
                description: payload.description ?? null,
                location: payload.location ?? null,
                attendees: payload.attendees ?? null,
            });
        },

        async update(
            id: string,
            patch: {
                title?: string;
                start?: string;
                end?: string;
                description?: string;
                location?: string;
                attendees?: string[];
            }
        ) {
            const session_id = getSessionId();
            await axiosInstance.patch(`/google/calendar/events/${id}`, {
                session_id,
                summary: patch.title ?? undefined,
                start: patch.start ?? undefined,
                end: patch.end ?? undefined,
                description: patch.description ?? undefined,
                location: patch.location ?? undefined,
                attendees: patch.attendees ?? undefined,
            });
        },

        async remove(id: string) {
            const session_id = getSessionId();
            await axiosInstance.delete(`/google/calendar/events/${id}`, {
                params: { session_id },
            });
        },
    },
});
