<!-- src/components/CalendarComponent.vue -->
<template>
    <div class="relative w-full h-full">
        <!-- 구글 연동 가림막 -->
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

        <!-- 캘린더 -->
        <FullCalendar
            v-show="user.authed && user.googleConnected"
            ref="fullcal"
            :options="options"
            class="h-full"
        />

        <!-- 생성/수정 모달 -->
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
                    <!-- 제목 -->
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

                    <!-- 시작 -->
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

                    <!-- 종료 -->
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

                    <!-- 위치 -->
                    <div>
                        <label class="mb-1 block text-sm text-gray-600"
                            >위치</label
                        >
                        <input
                            v-model="modal.location"
                            type="text"
                            class="w-full rounded border px-3 py-2"
                            placeholder="예: 본사 3층 대회의실"
                        />
                    </div>

                    <!-- 설명 -->
                    <div>
                        <label class="mb-1 block text-sm text-gray-600"
                            >설명</label
                        >
                        <textarea
                            v-model="modal.description"
                            class="w-full rounded border px-3 py-2 min-h-[88px]"
                            placeholder="회의 안건, 메모 등을 입력하세요"
                        ></textarea>
                    </div>

                    <!-- 참석자 (이메일만) -->
                    <div>
                        <div class="flex items-center justify-between">
                            <label class="mb-1 block text-sm text-gray-600"
                                >참석자 (이메일)</label
                            >
                            <span class="text-xs text-gray-400"
                                >이메일만 추가할 수 있어요</span
                            >
                        </div>
                        <div class="flex gap-2">
                            <input
                                v-model.trim="attendeeInput"
                                @keydown.enter.prevent="tryAddAttendee"
                                type="email"
                                class="flex-1 rounded border px-3 py-2"
                                placeholder="name@example.com 입력 후 Enter"
                            />
                            <button
                                class="rounded bg-gray-800 px-3 py-2 text-white disabled:opacity-50"
                                @click="tryAddAttendee"
                            >
                                추가
                            </button>
                        </div>
                        <p
                            v-if="attendeeError"
                            class="mt-1 text-xs text-red-600"
                        >
                            {{ attendeeError }}
                        </p>

                        <div
                            v-if="modal.attendees.length"
                            class="mt-2 flex flex-wrap gap-2"
                        >
                            <span
                                v-for="(email, idx) in modal.attendees"
                                :key="email + idx"
                                class="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1 text-sm"
                            >
                                {{ email }}
                                <button
                                    class="text-gray-500 hover:text-red-600"
                                    @click="removeAttendee(idx)"
                                >
                                    ✕
                                </button>
                            </span>
                        </div>

                        <!-- 초대 메일 발송 -->
                        <div
                            v-if="modal.attendees.length"
                            class="mt-3 flex items-center gap-2"
                        >
                            <input
                                id="notify"
                                type="checkbox"
                                v-model="modal.notifyAttendees"
                                class="h-4 w-4"
                            />
                            <label for="notify" class="text-sm text-gray-700"
                                >참석자에게 초대 메일 발송</label
                            >
                        </div>
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

import axiosInstance from "../utils/axiosInstance";
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

        // 참석자 입력 상태
        const attendeeInput = ref("");
        const attendeeError = ref<string | null>(null);
        const emailRegex =
            // 간단 이메일 검증 (RFC 완벽X, UI용)
            /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;

        const isValidEmail = (s: string) => emailRegex.test(s.trim());
        const tryAddAttendee = () => {
            attendeeError.value = null;
            const v = attendeeInput.value.trim();
            if (!v) return;
            if (!isValidEmail(v)) {
                attendeeError.value =
                    "이메일 형식으로 입력해주세요 (예: name@example.com)";
                return;
            }
            // 중복 방지
            if (!modal.attendees.includes(v)) {
                modal.attendees.push(v);
            }
            attendeeInput.value = "";
        };
        const removeAttendee = (idx: number) => {
            modal.attendees.splice(idx, 1);
        };

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
            location: string;
            description: string;
            attendees: string[];
            notifyAttendees: boolean;
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
            location: "",
            description: "",
            attendees: [],
            notifyAttendees: false,
        });

        // 서버 이벤트 → 풀캘 맵핑 (확장정보 보존)
        const mapFromBackend = (e: any) => {
            const start = e?.start?.dateTime || e?.start?.date || null;
            const end = e?.end?.dateTime || e?.end?.date || null;
            const allDay = Boolean(e?.start?.date && !e?.start?.dateTime);
            // 참석자 배열을 이메일 배열로 정리
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
                    attendees, // 이메일 문자열 배열
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
            modal.calendarId = "primary";
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
            modal.location = "";
            modal.description = "";
            modal.attendees = [];
            modal.notifyAttendees = false;
            attendeeInput.value = "";
            attendeeError.value = null;
        };

        const openEdit = (
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

            modal.location = extra?.location || "";
            modal.description = extra?.description || "";
            modal.attendees = Array.isArray(extra?.attendees)
                ? [...extra!.attendees!]
                : [];
            modal.notifyAttendees = false; // 기본값
            attendeeInput.value = "";
            attendeeError.value = null;
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
            modal.location = "";
            modal.description = "";
            modal.attendees = [];
            modal.notifyAttendees = false;
            attendeeInput.value = "";
            attendeeError.value = null;
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

        const saveModal = async () => {
            if (!modal.startLocal) return;
            saving.value = true;
            try {
                const s = new Date(modal.startLocal);
                const e = modal.endLocal ? new Date(modal.endLocal) : null;
                let { start, end } = ensureEndAfterStart(s, e);

                // 참석자 유효성 최종 점검
                if (modal.attendees.some((a) => !isValidEmail(a))) {
                    attendeeError.value =
                        "참석자 목록에 이메일 형식이 아닌 항목이 있어요. 수정 후 다시 저장해주세요.";
                    saving.value = false;
                    return;
                }

                const payload: any = {
                    summary: (modal.title || "일정").trim(),
                    start: start.toISOString(),
                    end: end.toISOString(),
                    attendees: [...modal.attendees],
                };
                if (modal.location?.trim())
                    payload.location = modal.location.trim();
                if (modal.description?.trim())
                    payload.description = modal.description.trim();

                // send_updates 파라미터: 참석자 있을 때만 전달
                const send_updates =
                    modal.attendees.length > 0
                        ? modal.notifyAttendees
                            ? "all"
                            : "none"
                        : undefined;

                if (modal.mode === "create") {
                    await axiosInstance.post(
                        "/google/calendar/events",
                        payload,
                        {
                            params: {
                                session_id: sessionId,
                                calendar_id: modal.calendarId || "primary",
                                ...(send_updates ? { send_updates } : {}),
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
            attendeeInput,
            attendeeError,
            tryAddAttendee,
            removeAttendee,
            closeModal,
            saveModal,
            deleteEvent,
        };
    },
});
</script>
