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
                        'rounded-lg px-4 py-2 max-w-md break-words text-left',
                        msg.role === 'user'
                            ? 'bg-[#646CFF] text-white'
                            : 'bg-white border border-gray-300 text-gray-800',
                    ]"
                >
                    <div
                        v-if="msg.role === 'assistant'"
                        class="assistant-content"
                        v-html="renderMarkdown(msg.content)"
                    />
                    <div v-else class="whitespace-pre-line">
                        {{ msg.content }}
                    </div>
                </div>
            </div>
        </div>

        <div class="border-t border-gray-200 p-4 bg-white">
            <form @submit.prevent="onFormSubmit" class="flex gap-2">
                <textarea
                    v-model="userInput"
                    ref="inputBox"
                    rows="1"
                    class="flex-1 resize-none overflow-y-auto max-h-40 border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400"
                    style="scrollbar-width: none"
                    placeholder="메시지를 입력하세요..."
                    @input="autoResize"
                    @keydown.enter.exact.prevent="onEnter"
                    @compositionstart="isComposing = true"
                    @compositionend="isComposing = false"
                ></textarea>

                <button
                    type="submit"
                    class="px-4 py-2 bg-[#646CFF] text-white rounded-md hover:bg-blue-600 disabled:opacity-50"
                    :disabled="isSending || !userInput.trim()"
                >
                    전송
                </button>
            </form>
        </div>
    </div>
</template>

<script lang="ts">
import { defineComponent, nextTick } from "vue";
import { chatSchedule } from "../api/chat_schedule";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";

const md = new MarkdownIt({
    breaks: true,
    linkify: true,
    html: false,
});

type ChatMsg = { role: "user" | "assistant"; content: string };

export default defineComponent({
    name: "ChatComponent",
    data() {
        return {
            userInput: "" as string,
            messages: [] as ChatMsg[],
            isSending: false,
            isComposing: false,
        };
    },
    methods: {
        renderMarkdown(text: string) {
            const html = md.render(text ?? "");
            return DOMPurify.sanitize(html);
        },

        autoResize() {
            const textarea = this.$refs.inputBox as
                | HTMLTextAreaElement
                | undefined;
            if (!textarea) return;
            textarea.style.height = "auto";
            textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px";
        },
        onEnter() {
            if (this.isComposing) return;
            this.onFormSubmit();
        },
        async onFormSubmit() {
            if (this.isSending) return;
            const content = this.userInput.trim();
            if (!content) return;

            this.isSending = true;

            this.messages.push({ role: "user", content });
            this.userInput = "";

            await nextTick();
            const textarea = this.$refs.inputBox as
                | HTMLTextAreaElement
                | undefined;
            if (textarea) textarea.style.height = "auto";
            this.scrollToBottom();

            try {
                const { reply } = await chatSchedule({
                    user_message: content,
                    history: this.messages
                        .slice(0, -1)
                        .map(({ role, content }) => ({ role, content })),
                });
                this.messages.push({ role: "assistant", content: reply });
            } catch (e) {
                this.messages.push({
                    role: "assistant",
                    content:
                        "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                });
            } finally {
                this.isSending = false;
                this.scrollToBottom();
            }
        },
        scrollToBottom() {
            nextTick(() => {
                const container = this.$refs.chatBody as
                    | HTMLElement
                    | undefined;
                if (container) container.scrollTop = container.scrollHeight;
            });
        },
    },
    mounted() {
        this.scrollToBottom();
    },
});
</script>

<style scoped>
:deep(.assistant-content ol) {
    list-style: decimal;
    list-style-position: inside;
    margin: 0.25rem 0;
    padding-left: 0;
}
:deep(.assistant-content ul) {
    list-style: disc;
    list-style-position: inside;
    margin: 0.25rem 0;
    padding-left: 0;
}
:deep(.assistant-content li) {
    margin: 0.125rem 0;
}
</style>
