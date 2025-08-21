<template>
    <div class="relative w-full h-full">
        <div
            v-if="!user.authed || !user.googleConnected"
            class="absolute inset-0 z-10 flex items-center justify-center bg-white/70 backdrop-blur-sm"
        >
            <div
                class="w-full max-w-md rounded-lg bg-white shadow p-6 text-center space-y-4"
            >
                <h3 class="text-lg font-semibold">
                    캘린더 사용을 위해 Google 연동이 필요합니다
                </h3>
                <p class="text-sm text-gray-600">
                    Google 계정으로 로그인하고 Google Calendar 연동을 완료해야
                    일정을 확인/추가할 수 있습니다.
                </p>
                <div class="flex items-center justify-center">
                    <button
                        class="px-4 py-2 rounded bg-[#4285F4] text-white disabled:opacity-50"
                        :disabled="user.busy"
                        @click="user.loginAndConnect()"
                    >
                        {{ user.busy ? "연결 중..." : "Google로 계속하기" }}
                    </button>
                </div>
            </div>
        </div>

        <FullCalendar
            v-show="user.authed && user.googleConnected"
            ref="fullcal"
            :options="options"
            class="h-full"
        />

        <calendar-modal
            :visible="modalOpen"
            :mode="modalMode"
            :saving="saving"
            :modelValue="form"
            @update:modelValue="updateForm"
            @close="closeModal"
            @save="handleSave"
            @delete="deleteEvent"
        />
    </div>
</template>

<script lang="ts">
import { defineComponent, ref, reactive, onMounted } from "vue";
import FullCalendar from "@fullcalendar/vue3";
import multiMonthPlugin from "@fullcalendar/multimonth";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import koLocale from "@fullcalendar/core/locales/ko";

import axiosInstance from "../utils/axiosInstance";
import { getSessionId } from "../utils/session";
import { useUserStore } from "../store/user";

import CalendarModal from "./CalendarModal.vue";

type Mode = "create" | "edit";

type EventForm = {
    title: string;
    startLocal: string;
    endLocal: string;
    location: string;
    description: string;
    attendees: string[];
    notifyAttendees: boolean;
};

export default defineComponent({
    name: "CalendarComponent",
    components: { FullCalendar, CalendarModal },

    setup() {
        const user = useUserStore();
        const fullcal = ref<InstanceType<typeof FullCalendar> | null>(null);
        const saving = ref(false);

        // 모달 상태/메타
        const modalOpen = ref(false);
        const modalMode = ref<Mode>("create");
        const current = reactive<{
            id: string | null;
            calendarId: string | null;
            fromAllDay: boolean;
            initialEndLocal: string;
        }>({
            id: null,
            calendarId: null,
            fromAllDay: false,
            initialEndLocal: "",
        });

        const form = reactive<EventForm>({
            title: "",
            startLocal: "",
            endLocal: "",
            location: "",
            description: "",
            attendees: [],
            notifyAttendees: false,
        });

        function updateForm(v: EventForm) {
            Object.assign(form, v);
        }

        const pad = (n: number) => String(n).padStart(2, "0");
        const toLocalInput = (isoLike?: string | null) => {
            if (!isoLike) return "";
            const d = new Date(isoLike);
            const y = d.getFullYear();
            const m = pad(d.getMonth() + 1);
            const day = pad(d.getDate());
            const hh = pad(d.getHours());
            const mm = pad(d.getMinutes());
            return `${y}-${m}-${day}T${hh}:${mm}`;
        };

        const oneHour = 60 * 60 * 1000;
        const ensureEndAfterStart = (start: Date, end: Date | null) => {
            const s = start.getTime();
            let e = end ? end.getTime() : s + oneHour;
            if (e <= s) e = s + oneHour;
            return { start: new Date(s), end: new Date(e) };
        };

        const mapFromBackend = (e: any) => {
            const start = e?.start?.dateTime || e?.start?.date || null;
            const end = e?.end?.dateTime || e?.end?.date || null;
            const allDay = Boolean(e?.start?.date && !e?.start?.dateTime);
            const attendees = Array.isArray(e?.attendees)
                ? e.attendees.map((a: any) => a?.email).filter(Boolean)
                : [];
            return {
                id: e.id,
                title: e.summary || "(제목 없음)",
                start,
                end,
                allDay,
                extendedProps: {
                    calendarId: e._calendarId || "primary",
                    location: e.location || "",
                    description: e.description || "",
                    attendees,
                },
            };
        };

        function openCreate(
            title = "",
            startStr = "",
            endStr: string | null = null,
            allDay = false
        ) {
            modalOpen.value = true;
            modalMode.value = "create";
            current.id = null;
            current.calendarId = "primary";
            current.fromAllDay = allDay;

            form.title = title;
            form.startLocal = toLocalInput(startStr);
            if (allDay) {
                const s = new Date(form.startLocal);
                const e = new Date(s.getTime() + oneHour);
                form.endLocal = toLocalInput(e.toISOString());
            } else {
                if (endStr) form.endLocal = toLocalInput(endStr);
                else {
                    const s = new Date(form.startLocal);
                    const e = new Date(s.getTime() + oneHour);
                    form.endLocal = toLocalInput(e.toISOString());
                }
            }
            current.initialEndLocal = form.endLocal;
            form.location = "";
            form.description = "";
            form.attendees = [];
            form.notifyAttendees = false;
        }

        function openEdit(
            id: string,
            title: string,
            startStr: string,
            endStr: string | null,
            calendarId: string,
            extra?: {
                location?: string;
                description?: string;
                attendees?: string[];
            }
        ) {
            modalOpen.value = true;
            modalMode.value = "edit";
            current.id = id;
            current.calendarId = calendarId || "primary";
            current.fromAllDay = false;

            form.title = title;
            form.startLocal = toLocalInput(startStr);
            form.endLocal = toLocalInput(endStr);
            current.initialEndLocal = form.endLocal;

            form.location = extra?.location || "";
            form.description = extra?.description || "";
            form.attendees = Array.isArray(extra?.attendees)
                ? [...extra!.attendees!]
                : [];
            form.notifyAttendees = false;
        }

        function closeModal() {
            modalOpen.value = false;
            modalMode.value = "create";
            current.id = null;
            current.calendarId = null;
            current.fromAllDay = false;
            current.initialEndLocal = "";
            Object.assign(form, {
                title: "",
                startLocal: "",
                endLocal: "",
                location: "",
                description: "",
                attendees: [],
                notifyAttendees: false,
            });
        }

        const sessionId = getSessionId();

        const options = ref<any>({
            plugins: [
                multiMonthPlugin,
                dayGridPlugin,
                timeGridPlugin,
                interactionPlugin,
            ],
            initialView: "multiMonthYear",
            multiMonthMaxColumns: 1,
            headerToolbar: {
                start: "prevYear,prev today next,nextYear",
                center: "title",
                end: "multiMonthYear,dayGridMonth,timeGridWeek,timeGridDay",
            },
            locales: [koLocale],
            locale: "ko",
            timeZone: "Asia/Seoul",
            nowIndicator: true,
            navLinks: true,
            selectable: true,
            editable: true,
            weekends: true,
            eventTimeFormat: { hour: "2-digit", minute: "2-digit" },

            events: async (info: any, success: any, failure: any) => {
                try {
                    if (!user.authed || !user.googleConnected) {
                        success([]);
                        return;
                    }
                    const { data } = await axiosInstance.get(
                        "/google/calendar/events",
                        {
                            params: {
                                session_id: sessionId,
                                timeMin: info.startStr,
                                timeMax: info.endStr,
                            },
                        }
                    );
                    const items = Array.isArray(data?.items)
                        ? data.items
                        : Array.isArray(data)
                        ? data
                        : [];
                    const events = items.map(mapFromBackend);
                    success(events);
                } catch (e) {
                    console.error(e);
                    failure(e);
                }
            },

            select: (info: any) => {
                if (!user.authed || !user.googleConnected) return;
                openCreate("", info.startStr, info.endStr, info.allDay);
                fullcal.value?.getApi().unselect();
            },

            eventClick: (arg: any) => {
                if (!user.authed || !user.googleConnected) return;
                const calId = arg.event.extendedProps?.calendarId || "primary";
                openEdit(
                    String(arg.event.id),
                    arg.event.title,
                    arg.event.startStr,
                    arg.event.endStr,
                    calId,
                    {
                        location: arg.event.extendedProps?.location || "",
                        description: arg.event.extendedProps?.description || "",
                        attendees: Array.isArray(
                            arg.event.extendedProps?.attendees
                        )
                            ? arg.event.extendedProps.attendees
                            : [],
                    }
                );
            },

            eventDrop: async (arg: any) => {
                if (!user.authed || !user.googleConnected) return;
                const calId = arg.event.extendedProps?.calendarId || "primary";
                await axiosInstance.patch(
                    `/google/calendar/events/${arg.event.id}`,
                    {
                        start: (arg.event.start as Date)?.toISOString(),
                        end: (arg.event.end as Date)?.toISOString() ?? null,
                    },
                    { params: { session_id: sessionId, calendar_id: calId } }
                );
                fullcal.value?.getApi().refetchEvents();
            },

            eventResize: async (arg: any) => {
                if (!user.authed || !user.googleConnected) return;
                const calId = arg.event.extendedProps?.calendarId || "primary";
                await axiosInstance.patch(
                    `/google/calendar/events/${arg.event.id}`,
                    {
                        start: (arg.event.start as Date)?.toISOString(),
                        end: (arg.event.end as Date)?.toISOString() ?? null,
                    },
                    { params: { session_id: sessionId, calendar_id: calId } }
                );
                fullcal.value?.getApi().refetchEvents();
            },
        });

        async function handleSave(payload: EventForm) {
            if (!payload.startLocal) return;
            saving.value = true;
            try {
                const s = new Date(payload.startLocal);
                const e = payload.endLocal ? new Date(payload.endLocal) : null;
                let { start, end } = ensureEndAfterStart(s, e);

                const payloadToSend: any = {
                    summary: (payload.title || "일정").trim(),
                    start: start.toISOString(),
                    end: end.toISOString(),
                    attendees: [...(payload.attendees || [])],
                };
                if (payload.location?.trim())
                    payloadToSend.location = payload.location.trim();
                if (payload.description?.trim())
                    payloadToSend.description = payload.description.trim();

                const send_updates =
                    (payload.attendees?.length ?? 0) > 0
                        ? payload.notifyAttendees
                            ? "all"
                            : "none"
                        : undefined;

                if (modalMode.value === "create") {
                    await axiosInstance.post(
                        "/google/calendar/events",
                        payloadToSend,
                        {
                            params: {
                                session_id: sessionId,
                                calendar_id: current.calendarId || "primary",
                                ...(send_updates ? { send_updates } : {}),
                            },
                        }
                    );
                } else if (current.id) {
                    await axiosInstance.patch(
                        `/google/calendar/events/${current.id}`,
                        payloadToSend,
                        {
                            params: {
                                session_id: sessionId,
                                calendar_id: current.calendarId || "primary",
                                ...(send_updates ? { send_updates } : {}),
                            },
                        }
                    );
                }

                closeModal();
                fullcal.value?.getApi().refetchEvents();
            } catch (e) {
                console.error(e);
            } finally {
                saving.value = false;
            }
        }

        async function deleteEvent() {
            if (modalMode.value !== "edit" || !current.id) return;
            saving.value = true;
            try {
                await axiosInstance.delete(
                    `/google/calendar/events/${current.id}`,
                    {
                        params: {
                            session_id: sessionId,
                            calendar_id: current.calendarId || "primary",
                        },
                    }
                );
                closeModal();
                fullcal.value?.getApi().refetchEvents();
            } catch (e) {
                console.error(e);
            } finally {
                saving.value = false;
            }
        }

        onMounted(() => {
            if (user.authed && user.googleConnected) {
                fullcal.value?.getApi().refetchEvents();
            }
        });

        return {
            user,
            fullcal,
            options,
            modalOpen,
            modalMode,
            form,
            updateForm,
            closeModal,
            handleSave,
            deleteEvent,
            saving,
        };
    },
});
</script>
