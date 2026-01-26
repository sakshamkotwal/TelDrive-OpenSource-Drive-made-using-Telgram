const { createApp, ref, computed, onMounted, reactive, watch } = Vue;

async function deriveKeyHex(password, salt) {
    const enc = new TextEncoder();
    const keyMaterial = await window.crypto.subtle.importKey(
        "raw", enc.encode(password), {name: "PBKDF2"}, false, ["deriveBits"]
    );
    // Salt logic: for MVP we use a fixed salt prefix + channelID if available, or just fixed.
    // To ensure portability, let's use a fixed salt for now: "teledrive_secure_salt"
    // In production, salt should be unique per drive.
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
        const newFolderName = ref('');

        const uploading = ref(false);
        const uploadProgress = ref(0);

        // Navigation history for breadcrumbs
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
        const breadcrumbs = computed(() => path.value);

        // Check if we are already logged in
        if (session.channelId && session.encryptionKey) {
            session.ready = true;
            fetchFiles();
        }

        // Methods
        async function verifyChannel() {
            loading.value = true;
            error.value = '';
            try {
                // Verify against backend
                // Note: verification endpoint doesn't need auth, just checks bot access
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
                // We don't verify against server here strictly (stateless),
                // but we can try to fetch files. If fails (decryption or bad request), then wrong password?
                // Actually server doesn't verify key on list_files (dataset not encrypted).
                // So list_files works even with wrong key!
                // Key is only verified when downloading/uploading.
                // We'll assume success for now.

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

                const res = await fetch(url, {
                    headers: {
                        'X-Channel-ID': session.channelId,
                        'X-Encryption-Key': session.encryptionKey
                    }
                });
                if (!res.ok) throw new Error("Failed to fetch files");
                const data = await res.json();
                files.value = data.files;
                folders.value = data.folders;
                currentFolderId.value = folderId;
            } catch (e) {
                console.error(e);
                error.value = e.message;
            } finally {
                loadingFiles.value = false;
            }
        }

        function navigate(folderId) {
            if (folderId === null) {
                path.value = [];
            } else {
                // Find folder name
                const folder = folders.value.find(f => f.id === folderId);
                // If not in current list, maybe we can't find name easily without full tree.
                // For MVP, we just push valid folder.
                if (folder) path.value.push(folder);
            }
            fetchFiles(folderId);
        }

        async function handleUpload(event) {
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

                    // We use XMLHttpRequest for progress
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
            if(!confirm("Are you sure?")) return;
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

        function getFileIcon(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            if (['jpg', 'jpeg', 'png', 'gif'].includes(ext)) return 'fas fa-image text-purple-500';
            if (['pdf'].includes(ext)) return 'fas fa-file-pdf text-red-500';
            if (['zip', 'rar', '7z'].includes(ext)) return 'fas fa-file-archive text-yellow-600';
            if (['mp4', 'mov'].includes(ext)) return 'fas fa-video text-pink-500';
            if (['mp3', 'wav'].includes(ext)) return 'fas fa-music text-green-500';
            return 'fas fa-file text-gray-400';
        }

        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        return {
            session, step, form, error, loading,
            files, folders, loadingFiles, currentFolderId,
            filteredFiles, filteredFolders, breadcrumbs, searchQuery,
            showCreateFolder, newFolderName, uploading, uploadProgress,
            verifyChannel, login, logout, navigate,
            handleUpload, downloadFile, createFolder, deleteItem,
            getFileIcon, formatSize
        }
    }
}).mount('#app');
