<template>
    <div
        v-if="visible"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
        <div class="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
            <div class="mb-4 flex items-center justify-between">
                <h3 class="text-lg font-semibold">
                    {{ mode === "create" ? "일정 추가" : "일정 수정" }}
                </h3>
                <button
                    class="rounded px-2 py-1 text-gray-500 hover:bg-gray-100"
                    @click="emit('close')"
                >
                    ✕
                </button>
            </div>

            <div class="space-y-3">
                <div>
                    <label class="mb-1 block text-sm text-gray-600">제목</label>
                    <input
                        v-model="local.title"
                        type="text"
                        class="w-full rounded border px-3 py-2"
                        placeholder="제목을 입력하세요"
                    />
                </div>

                <div>
                    <label class="mb-1 block text-sm text-gray-600">시작</label>
                    <input
                        v-model="local.startLocal"
                        type="datetime-local"
                        class="w-full rounded border px-3 py-2"
                    />
                </div>

                <div>
                    <label class="mb-1 block text-sm text-gray-600">종료</label>
                    <input
                        v-model="local.endLocal"
                        type="datetime-local"
                        class="w-full rounded border px-3 py-2"
                    />
                    <p class="mt-1 text-xs text-gray-500">
                        종료가 비어있거나 시작보다 빠르면 자동으로
                        <b>시작+1시간</b>으로 저장됩니다.
                    </p>
                </div>

                <div>
                    <label class="mb-1 block text-sm text-gray-600">위치</label>
                    <input
                        v-model="local.location"
                        type="text"
                        class="w-full rounded border px-3 py-2"
                        placeholder="예: 서울시 강남구 역삼동"
                    />
                </div>

                <div>
                    <label class="mb-1 block text-sm text-gray-600">설명</label>
                    <textarea
                        v-model="local.description"
                        class="w-full rounded border px-3 py-2 min-h-[88px]"
                        placeholder="회의 안건, 메모 등을 입력하세요"
                    ></textarea>
                </div>

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

                    <p v-if="attendeeError" class="mt-1 text-xs text-red-600">
                        {{ attendeeError }}
                    </p>

                    <div
                        v-if="local.attendees.length"
                        class="mt-2 flex flex-wrap gap-2"
                    >
                        <span
                            v-for="(email, idx) in local.attendees"
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

                    <div
                        v-if="local.attendees.length"
                        class="mt-3 flex items-center gap-2"
                    >
                        <input
                            id="notify"
                            type="checkbox"
                            v-model="local.notifyAttendees"
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
                    v-if="mode === 'edit'"
                    class="rounded border px-4 py-2 text-red-600 border-red-300 hover:bg-red-50 disabled:opacity-50"
                    :disabled="saving"
                    @click="emit('delete')"
                >
                    삭제
                </button>

                <div class="ml-auto flex gap-2">
                    <button
                        class="rounded border px-4 py-2"
                        :disabled="saving"
                        @click="emit('close')"
                    >
                        취소
                    </button>
                    <button
                        class="rounded bg-[#646CFF] px-4 py-2 text-white disabled:opacity-50"
                        :disabled="saving"
                        @click="onSave"
                    >
                        {{ saving ? "저장 중..." : "저장" }}
                    </button>
                </div>
            </div>
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent, reactive, ref, watch, computed } from "vue";
import type { PropType } from "vue";

type Mode = "create" | "edit";
export interface EventForm {
    title: string;
    startLocal: string;
    endLocal: string;
    location: string;
    description: string;
    attendees: string[];
    notifyAttendees: boolean;
}

export default defineComponent({
    name: "EventEditorModal",
    props: {
        visible: { type: Boolean, required: true },
        mode: { type: String as PropType<Mode>, required: true },
        saving: { type: Boolean, default: false },
        modelValue: { type: Object as PropType<EventForm>, required: true },
    },
    emits: ["update:modelValue", "close", "save", "delete"],
    setup(props, { emit }) {
        const mode = computed(() => props.mode);
        const visible = computed(() => props.visible);
        const saving = computed(() => props.saving ?? false);

        const local = reactive<EventForm>({
            title: "",
            startLocal: "",
            endLocal: "",
            location: "",
            description: "",
            attendees: [],
            notifyAttendees: false,
        });

        watch(
            () => props.visible,
            (v) => {
                if (v) Object.assign(local, props.modelValue);
            }
        );

        watch(
            () => props.modelValue,
            (v) => {
                Object.assign(local, v);
            },
            { deep: true }
        );

        watch(local, (v) => emit("update:modelValue", { ...v }), {
            deep: true,
        });

        const attendeeInput = ref("");
        const attendeeError = ref<string | null>(null);
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;
        const isValidEmail = (s: string) => emailRegex.test(s.trim());

        function tryAddAttendee() {
            attendeeError.value = null;
            const v = attendeeInput.value.trim();
            if (!v) return;
            if (!isValidEmail(v)) {
                attendeeError.value =
                    "이메일 형식으로 입력해주세요 (예: name@example.com)";
                return;
            }
            if (!local.attendees.includes(v)) local.attendees.push(v);
            attendeeInput.value = "";
        }

        function removeAttendee(idx: number) {
            local.attendees.splice(idx, 1);
        }

        function onSave() {
            if (!local.startLocal) {
                attendeeError.value = "시작 시간을 입력해주세요";
                return;
            }

            emit("save", { ...local });
        }

        return {
            mode,
            visible,
            saving,
            local,
            attendeeInput,
            attendeeError,
            tryAddAttendee,
            removeAttendee,
            onSave,
            emit,
        };
    },
});
</script>
