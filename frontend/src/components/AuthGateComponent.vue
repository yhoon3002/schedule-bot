<template>
    <div
        class="w-screen min-h-screen flex items-center justify-center bg-[#f8f8ff]"
    >
        <div class="w-full max-w-md rounded-lg bg-white shadow p-6">
            <h1 class="text-xl font-semibold mb-2">AI ScheduleBot</h1>
            <p class="text-gray-600 mb-6">
                사용을 위해 Google 로그인과 Google 캘린더 연동이 필요합니다.
            </p>

            <button
                class="w-full rounded bg-[#4285F4] text-white py-3 disabled:opacity-50"
                :disabled="user.busy"
                @click="start"
            >
                {{ user.busy ? "처리 중..." : "Google로 계속하기" }}
            </button>

            <p class="text-xs text-gray-500 mt-4">
                팝업 차단을 해제해 주세요. 최초 한 번만 동의하시면 됩니다.
            </p>
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";
import { useUserStore } from "../store/user";

export default defineComponent({
    name: "AuthGateComponent",
    setup() {
        const user = useUserStore();
        const start = async () => {
            await user.loginAndConnect();
        };

        return { user, start };
    },
});
</script>
