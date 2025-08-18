<!-- src/components/CalendarComponent.vue -->
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

        <div
            v-if="modal.open"
            class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        >
            <div class="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
                <div class="mb-4 flex items-center justify-between">
                    <h3 class="text-lg font-semibold">
                        {{
                            modal.mode === "create" ? "일정 추가" : "일정 수정"
                        }}
                    </h3>
                    <button
                        class="rounded px-2 py-1 text-gray-500 hover:bg-gray-100"
                        @click="closeModal"
                    >
                        ✕
                    </button>
                </div>

                <div class="space-y-3">
                    <div>
                        <label class="mb-1 block text-sm text-gray-600"
                            >제목</label
                        >
                        <input
                            v-model="modal.title"
                            type="text"
                            class="w-full rounded border px-3 py-2"
                            placeholder="제목을 입력하세요"
                        />
                    </div>

                    <div>
                        <label class="mb-1 block text-sm text-gray-600"
                            >시작</label
                        >
                        <input
                            v-model="modal.startLocal"
                            type="datetime-local"
                            class="w-full rounded border px-3 py-2"
                        />
                    </div>

                    <div>
                        <label class="mb-1 block text-sm text-gray-600"
                            >종료</label
                        >
                        <input
                            v-model="modal.endLocal"
                            type="datetime-local"
                            class="w-full rounded border px-3 py-2"
                        />
                        <p class="mt-1 text-xs text-gray-500">
                            종료가 비어있거나 시작보다 빠르면 자동으로
                            <b>시작+1시간</b>으로 저장됩니다.
                        </p>
                    </div>
                </div>

                <div class="mt-5 flex justify-between">
                    <button
                        v-if="modal.mode === 'edit'"
                        class="rounded border px-4 py-2 text-red-600 border-red-300 hover:bg-red-50 disabled:opacity-50"
                        :disabled="saving"
                        @click="deleteEvent"
                    >
                        삭제
                    </button>

                    <div class="ml-auto flex gap-2">
                        <button
                            class="rounded border px-4 py-2"
                            :disabled="saving"
                            @click="closeModal"
                        >
                            취소
                        </button>
                        <button
                            class="rounded bg-[#646CFF] px-4 py-2 text-white disabled:opacity-50"
                            :disabled="saving"
                            @click="saveModal"
                        >
                            {{ saving ? "저장 중..." : "저장" }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent, ref, reactive, watch, onMounted } from "vue";
import FullCalendar from "@fullcalendar/vue3";
import multiMonthPlugin from "@fullcalendar/multimonth";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import koLocale from "@fullcalendar/core/locales/ko";

import axiosInstance from "../utils/axios";
import { getSessionId } from "../utils/session";
import { useUserStore } from "../store/user";

type Mode = "create" | "edit";

export default defineComponent({
    name: "CalendarComponent",
    components: { FullCalendar },

    setup() {
        const user = useUserStore();
        const fullcal = ref<InstanceType<typeof FullCalendar> | null>(null);
        const saving = ref(false);

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

        const modal = reactive<{
            open: boolean;
            mode: Mode;
            id: string | null;
            calendarId: string | null;
            title: string;
            startLocal: string;
            endLocal: string;
            fromAllDay: boolean;
            initialEndLocal: string;
        }>({
            open: false,
            mode: "create",
            id: null,
            calendarId: null,
            title: "",
            startLocal: "",
            endLocal: "",
            fromAllDay: false,
            initialEndLocal: "",
        });

        // ✅ 구글 원본 → FullCalendar 매핑 (캘린더ID를 extendedProps로 보존)
        const mapFromBackend = (e: any) => {
            const start = e?.start?.dateTime || e?.start?.date || null;
            const end = e?.end?.dateTime || e?.end?.date || null;
            const allDay = Boolean(e?.start?.date && !e?.start?.dateTime);
            return {
                id: e.id,
                title: e.summary || "(제목 없음)",
                start,
                end,
                allDay,
                extendedProps: {
                    calendarId: e._calendarId || "primary",
                },
            };
        };

        const openCreate = (
            title = "",
            startStr = "",
            endStr: string | null = null,
            allDay = false
        ) => {
            modal.open = true;
            modal.mode = "create";
            modal.id = null;
            modal.calendarId = "primary"; // 새 일정은 기본 primary에 생성
            modal.title = title;
            modal.startLocal = toLocalInput(startStr);

            if (allDay) {
                const s = new Date(modal.startLocal);
                const e = new Date(s.getTime() + oneHour);
                modal.endLocal = toLocalInput(e.toISOString());
                modal.fromAllDay = true;
            } else {
                if (endStr) {
                    modal.endLocal = toLocalInput(endStr);
                } else {
                    const s = new Date(modal.startLocal);
                    const e = new Date(s.getTime() + oneHour);
                    modal.endLocal = toLocalInput(e.toISOString());
                }
                modal.fromAllDay = false;
            }
            modal.initialEndLocal = modal.endLocal;
        };

        const openEdit = (
            id: string,
            title: string,
            startStr: string,
            endStr: string | null,
            calendarId: string
        ) => {
            modal.open = true;
            modal.mode = "edit";
            modal.id = id;
            modal.calendarId = calendarId || "primary";
            modal.title = title;
            modal.startLocal = toLocalInput(startStr);
            modal.endLocal = toLocalInput(endStr);
            modal.fromAllDay = false;
            modal.initialEndLocal = modal.endLocal;
        };

        const closeModal = () => {
            modal.open = false;
            modal.mode = "create";
            modal.id = null;
            modal.calendarId = null;
            modal.title = "";
            modal.startLocal = "";
            modal.endLocal = "";
            modal.fromAllDay = false;
            modal.initialEndLocal = "";
        };

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
                    calId
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
                    { params: { session_id: sessionId, calendar_id: calId } } // 👈 캘린더ID 전달
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
                    { params: { session_id: sessionId, calendar_id: calId } } // 👈 캘린더ID 전달
                );
                fullcal.value?.getApi().refetchEvents();
            },
        });

        const saveModal = async () => {
            if (!modal.startLocal) return;
            saving.value = true;
            try {
                const s = new Date(modal.startLocal);
                const e = modal.endLocal ? new Date(modal.endLocal) : null;
                let { start, end } = ensureEndAfterStart(s, e);

                const payload = {
                    summary: (modal.title || "일정").trim(),
                    start: start.toISOString(),
                    end: end.toISOString(),
                };

                if (modal.mode === "create") {
                    // 새 일정은 기본 primary에 생성 (원한다면 선택 UI로 확장 가능)
                    await axiosInstance.post(
                        "/google/calendar/events",
                        payload,
                        {
                            params: {
                                session_id: sessionId,
                                calendar_id: modal.calendarId || "primary",
                            },
                        }
                    );
                } else if (modal.id) {
                    await axiosInstance.patch(
                        `/google/calendar/events/${modal.id}`,
                        payload,
                        {
                            params: {
                                session_id: sessionId,
                                calendar_id: modal.calendarId || "primary",
                            }, // 👈 중요
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
        };

        const deleteEvent = async () => {
            if (modal.mode !== "edit" || !modal.id) return;
            saving.value = true;
            try {
                await axiosInstance.delete(
                    `/google/calendar/events/${modal.id}`,
                    {
                        params: {
                            session_id: sessionId,
                            calendar_id: modal.calendarId || "primary",
                        }, // 👈 중요
                    }
                );
                closeModal();
                fullcal.value?.getApi().refetchEvents();
            } catch (e) {
                console.error(e);
            } finally {
                saving.value = false;
            }
        };

        watch(
            () => [user.authed, user.googleConnected],
            ([a, c]) => {
                const api = fullcal.value?.getApi();
                if (a && c) api?.refetchEvents();
                else api?.removeAllEvents();
            }
        );

        onMounted(() => {
            if (user.authed && user.googleConnected) {
                fullcal.value?.getApi().refetchEvents();
            }
        });

        return {
            user,
            fullcal,
            options,
            modal,
            saving,
            closeModal,
            saveModal,
            deleteEvent,
        };
    },
});
</script>
