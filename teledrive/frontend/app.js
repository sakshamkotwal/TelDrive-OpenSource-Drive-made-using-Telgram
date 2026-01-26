const { createApp, ref, computed, onMounted, reactive, watch, nextTick } = Vue;

async function deriveKeyHex(password, salt) {
    const enc = new TextEncoder();
    const keyMaterial = await window.crypto.subtle.importKey(
        "raw", enc.encode(password), {name: "PBKDF2"}, false, ["deriveBits"]
    );
    const derivedBits = await window.crypto.subtle.deriveBits(
        {
            name: "PBKDF2",
            salt: enc.encode(salt || "teledrive_secure_salt"),
            iterations: 100000,
            hash: "SHA-256"
        },
        keyMaterial,
        256
    );
    return Array.from(new Uint8Array(derivedBits))
        .map(b => b.toString(16).padStart(2, '0')).join('');
}

createApp({
    setup() {
        const session = reactive({
            ready: false,
            channelId: localStorage.getItem('teledrive_channel_id') || '',
            encryptionKey: sessionStorage.getItem('teledrive_key') || ''
        });

        const step = ref(session.channelId ? 2 : 1);
        const form = reactive({ channelId: session.channelId, password: '' });
        const error = ref('');
        const loading = ref(false);

        const currentFolderId = ref(null);
        const files = ref([]);
        const folders = ref([]);
        const loadingFiles = ref(false);
        const searchQuery = ref('');
        const showCreateFolder = ref(false);
        const showUploadMenu = ref(false);
        const selectedItem = ref(null);
        const newFolderName = ref('');
        const folderInput = ref(null);

        const uploading = ref(false);
        const uploadProgress = ref(0);
        const sortOrder = ref('asc');

        const path = ref([]);

        // Computed
        const filteredFiles = computed(() => {
            if (!searchQuery.value) return files.value;
            return files.value.filter(f => f.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
        });
        const filteredFolders = computed(() => {
            if (!searchQuery.value) return folders.value;
            return folders.value.filter(f => f.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
        });
        const filteredItems = computed(() => {
            return [...filteredFolders.value, ...filteredFiles.value];
        });
        const breadcrumbs = computed(() => path.value);

        if (session.channelId && session.encryptionKey) {
            session.ready = true;
            fetchFiles();
        }

        // Methods
        function toggleSort() {
            sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc';
            // Simple sort logic
            const modifier = sortOrder.value === 'asc' ? 1 : -1;
            files.value.sort((a, b) => a.name.localeCompare(b.name) * modifier);
            folders.value.sort((a, b) => a.name.localeCompare(b.name) * modifier);
        }

        async function verifyChannel() {
            loading.value = true;
            error.value = '';
            try {
                const res = await fetch(`/api/setup/verify?channel_id=${form.channelId}`, { method: 'POST' });
                if (!res.ok) throw new Error((await res.json()).detail);

                session.channelId = form.channelId;
                localStorage.setItem('teledrive_channel_id', form.channelId);
                step.value = 2;
            } catch (e) {
                error.value = e.message;
            } finally {
                loading.value = false;
            }
        }

        async function login() {
            loading.value = true;
            error.value = '';
            try {
                const key = await deriveKeyHex(form.password, "teledrive_salt_" + session.channelId);
                session.encryptionKey = key;
                sessionStorage.setItem('teledrive_key', key);
                session.ready = true;
                fetchFiles();
            } catch (e) {
                error.value = e.message;
            } finally {
                loading.value = false;
            }
        }

        function logout() {
            session.ready = false;
            session.encryptionKey = '';
            sessionStorage.removeItem('teledrive_key');
            step.value = 2;
            form.password = '';
        }

        async function fetchFiles(folderId = null) {
            loadingFiles.value = true;
            try {
                const url = folderId
                    ? `/api/files?folder_id=${folderId}`
                    : '/api/files';

                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 15000);

                const res = await fetch(url, {
                    headers: {
                        'X-Channel-ID': session.channelId,
                        'X-Encryption-Key': session.encryptionKey
                    },
                    signal: controller.signal
                });
                clearTimeout(timeoutId);

                if (!res.ok) throw new Error("Failed to fetch files");
                const data = await res.json();

                // Add type property for easier rendering
                files.value = data.files.map(f => ({...f, type: 'file'}));
                folders.value = data.folders.map(f => ({...f, type: 'folder'}));
                currentFolderId.value = folderId;
            } catch (e) {
                console.error(e);
                error.value = e.name === 'AbortError' ? "Request timed out" : e.message;
            } finally {
                loadingFiles.value = false;
            }
        }

        function navigate(folderId) {
            if (folderId === null) {
                path.value = [];
            } else {
                const folder = folders.value.find(f => f.id === folderId);
                if (folder) path.value.push(folder);
            }
            fetchFiles(folderId);
        }

        async function handleUpload(event) {
            showUploadMenu.value = false;
            const fileList = event.target.files;
            if (!fileList.length) return;

            uploading.value = true;
            uploadProgress.value = 0;

            try {
                for (let file of fileList) {
                    const formData = new FormData();
                    formData.append('file', file);
                    if (currentFolderId.value) {
                        formData.append('parent_id', currentFolderId.value);
                    }

                    await new Promise((resolve, reject) => {
                        const xhr = new XMLHttpRequest();
                        xhr.open('POST', '/api/files/upload');
                        xhr.setRequestHeader('X-Channel-ID', session.channelId);
                        xhr.setRequestHeader('X-Encryption-Key', session.encryptionKey);

                        xhr.upload.onprogress = (e) => {
                            if (e.lengthComputable) {
                                uploadProgress.value = Math.round((e.loaded / e.total) * 100);
                            }
                        };

                        xhr.onload = () => {
                            if (xhr.status >= 200 && xhr.status < 300) resolve();
                            else reject(new Error(xhr.responseText));
                        };
                        xhr.onerror = () => reject(new Error("Network Error"));

                        xhr.send(formData);
                    });
                }
                fetchFiles(currentFolderId.value);
            } catch (e) {
                alert("Upload failed: " + e.message);
            } finally {
                uploading.value = false;
            }
        }

        async function downloadFile(file) {
            try {
                const res = await fetch(`/api/files/download/${file.id}`, {
                    headers: {
                        'X-Channel-ID': session.channelId,
                        'X-Encryption-Key': session.encryptionKey
                    }
                });
                if (!res.ok) throw new Error("Download failed");

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = file.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            } catch (e) {
                alert(e.message);
            }
        }

        function showCreateFolderModal() {
            showUploadMenu.value = false;
            showCreateFolder.value = true;
            nextTick(() => {
                // Focus input
                if(folderInput.value) folderInput.value.focus();
            });
        }

        async function createFolder() {
            if (!newFolderName.value) return;
            try {
                const formData = new FormData();
                formData.append('name', newFolderName.value);
                if (currentFolderId.value) formData.append('parent_id', currentFolderId.value);

                const res = await fetch('/api/folders', {
                    method: 'POST',
                    headers: {
                        'X-Channel-ID': session.channelId,
                        'X-Encryption-Key': session.encryptionKey
                    },
                    body: formData
                });
                if (!res.ok) throw new Error("Failed");

                showCreateFolder.value = false;
                newFolderName.value = '';
                fetchFiles(currentFolderId.value);
            } catch (e) {
                alert(e.message);
            }
        }

        async function deleteItem(itemId) {
            if(!confirm("Are you sure you want to delete this item?")) return;
            try {
                const res = await fetch(`/api/items/${itemId}`, {
                    method: 'DELETE',
                    headers: {
                        'X-Channel-ID': session.channelId,
                        'X-Encryption-Key': session.encryptionKey
                    }
                });
                if (!res.ok) throw new Error("Failed");
                fetchFiles(currentFolderId.value);
            } catch (e) {
                alert(e.message);
            }
        }

        function showItemOptions(item) {
            selectedItem.value = item;
        }

        function previewFile(file) {
            // For now just download or show options
            showItemOptions(file);
        }

        function getFileIcon(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            if (['jpg', 'jpeg', 'png', 'gif'].includes(ext)) return 'fas fa-image text-[#ea4335]'; // Google uses red/orange for images
            if (['pdf'].includes(ext)) return 'fas fa-file-pdf text-[#ea4335]';
            if (['zip', 'rar', '7z'].includes(ext)) return 'fas fa-file-archive text-[#fbbc04]';
            if (['mp4', 'mov'].includes(ext)) return 'fas fa-video text-[#ea4335]';
            if (['mp3', 'wav'].includes(ext)) return 'fas fa-music text-[#34a853]';
            if (['doc', 'docx'].includes(ext)) return 'fas fa-file-word text-[#4285f4]';
            if (['xls', 'xlsx'].includes(ext)) return 'fas fa-file-excel text-[#34a853]';
            return 'fas fa-file text-[#4285f4]'; // Default blue
        }

        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function formatDate(timestamp) {
             if (!timestamp) return '';
             const date = new Date(timestamp * 1000);
             const options = { month: 'short', day: 'numeric' };
             return date.toLocaleDateString('en-US', options);
        }

        return {
            session, step, form, error, loading,
            files, folders, loadingFiles, currentFolderId,
            filteredFiles, filteredFolders, filteredItems, breadcrumbs, searchQuery,
            showCreateFolder, showUploadMenu, selectedItem, newFolderName, folderInput,
            uploading, uploadProgress,
            verifyChannel, login, logout, navigate,
            handleUpload, downloadFile, createFolder, deleteItem,
            showCreateFolderModal, showItemOptions, previewFile,
            getFileIcon, formatSize, formatDate, toggleSort
        }
    }
}).mount('#app');
