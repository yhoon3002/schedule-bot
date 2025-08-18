<template>
    <div
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    >
        <div class="w-full max-w-md rounded bg-white p-5 shadow">
            <h3 class="text-lg font-semibold mb-2">구글 캘린더 연동</h3>
            <p class="text-sm text-gray-600">
                구글 캘린더와 연동하면 일정이 자동 동기화됩니다. 지금
                연동하시겠어요?
            </p>
            <div class="mt-5 flex justify-end gap-2">
                <button class="px-4 py-2 rounded border" @click="$emit('no')">
                    나중에
                </button>
                <button
                    class="px-4 py-2 rounded bg-[#4285F4] text-white"
                    @click="$emit('yes')"
                >
                    연동하기
                </button>
            </div>
        </div>
    </div>
</template>

<script lang="ts">
export default { name: "ConnectGoogleModal" };
</script>
