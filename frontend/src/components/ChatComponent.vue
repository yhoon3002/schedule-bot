<template>
    <div class="flex flex-col h-screen w-full max-w-3xl mx-auto bg-[#f8f8ff]">
        <div ref="chatBody" class="flex-1 overflow-y-auto p-4 space-y-4">
            <div
                v-for="(msg, index) in messages"
                :key="index"
                :class="[
                    'flex',
                    msg.role === 'user' ? 'justify-end' : 'justify-start',
                ]"
            >
                <div
                    :class="[
                        'rounded-lg px-4 py-2 max-w-md break-words whitespace-pre-line text-left',
                        msg.role === 'user'
                            ? 'bg-[#646CFF] text-white'
                            : 'bg-white border border-gray-300 text-gray-800',
                    ]"
                >
                    {{ msg.content }}
                </div>
            </div>
        </div>

        <div class="border-t border-gray-200 p-4 bg-white">
            <form @submit.prevent="sendMessage" class="flex gap-2">
                <textarea
                    v-model="userInput"
                    ref="inputBox"
                    rows="1"
                    class="flex-1 resize-none overflow-y-auto max-h-40 border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400"
                    style="scrollbar-width: none"
                    placeholder="메시지를 입력하세요..."
                    @input="autoResize"
                ></textarea>
                <button
                    type="submit"
                    class="px-4 py-2 bg-[#646CFF] text-white rounded-md hover:bg-blue-600"
                >
                    전송
                </button>
            </form>
        </div>
    </div>
</template>

<script lang="ts">
export default {
    data() {
        return {
            userInput: "",
            messages: [
                {
                    role: "assistant",
                    content: "안녕하세요! 무엇을 도와드릴까요?",
                },
                { role: "user", content: "일정 등록하고 싶어요." },
                {
                    role: "assistant",
                    content: "알겠습니다. 날짜를 알려주세요!",
                },
            ],
        };
    },

    methods: {
        autoResize() {
            const textarea = this.$refs.inputBox as HTMLTextAreaElement;
            if (!textarea) return;

            textarea.style.height = "auto"; // 초기화
            textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px"; // 최대 높이 제한
        },

        sendMessage() {
            const content = this.userInput.trim();
            if (!content) return;

            this.messages.push({ role: "user", content });

            this.userInput = "";

            this.$nextTick(() => {
                const textarea = this.$refs.inputBox as HTMLTextAreaElement;
                if (textarea) {
                    textarea.style.height = "auto";
                }
                this.scrollToBottom();
            });

            setTimeout(() => {
                this.messages.push({
                    role: "assistant",
                    content: "좋습니다. 다음 질문으로 넘어갈게요!",
                });
                this.scrollToBottom();
            }, 800);
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const container = this.$refs.chatBody as HTMLElement;
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            });
        },
    },

    mounted() {
        this.scrollToBottom();
    },
};
</script>
