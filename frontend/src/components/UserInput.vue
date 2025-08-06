<template>
    <div class="max-w-xl mx-auto p-4 space-y-8">
        <h1 class="text-xl font-bold">AI-Schedule-Bot</h1>
        <input
            v-model="userInput"
            type="text"
            placeholder="예: 내일 오후 3시에 회의"
            class="w-full p-2 border rounded"
        />
        <button
            @click="submitInput"
            class="text-black px-4 py-2 rounded bg-indigo-500 hover:bg-blue-600"
        >
            생성 요청
        </button>

        <div v-if="response" class="mt-4 p-4 border rounded bg-gray-100">
            <p><strong>제목:</strong> {{ response.title }}</p>
            <p><strong>날짜/시간:</strong> {{ response.datetime }}</p>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import axios from "axios";

const userInput = ref("");
const response = ref<{ title: string; datetime: string } | null>(null);

const submitInput = async () => {
    try {
        const res = await axios.post("http://localhost:8000/parse_schedule", {
            text: userInput.value,
        });
        response.value = res.data;
    } catch (err) {
        console.error("API 요청 실패:", err);
    }
};
</script>
