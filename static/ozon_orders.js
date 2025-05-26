document.addEventListener('DOMContentLoaded', function () {
    const table = document.getElementById('ozonOrdersTable');
    if (!table) return; 

    const filterInputs = document.querySelectorAll('#filterRow input[type="text"]');
    const tbody = table.querySelector('tbody');
    let tableRows = Array.from(tbody.querySelectorAll('tr')); 
    const selectAllCheckbox = document.getElementById('selectAllRows');
    const rowSelectorCheckboxes = document.querySelectorAll('.row-selector');
    const getCdekLabelsBtn = document.getElementById('getCdekLabelsBtn');
    const LOADER_DIV = document.getElementById("loader");
    const USER_MESSAGES_DIV = document.getElementById("user-messages");
    const CLEAR_REQUESTED_LABELS_BTN = document.getElementById("clearRequestedLabelsBtn");
    const DOWNLOAD_STATUS_FILTER_SELECT = document.getElementById("downloadStatusFilter");
    const SELECTED_ORDERS_INFO_SPAN = document.getElementById("selectedOrdersInfo");
    const HIGHLIGHT_DUPLICATES_TOGGLE = document.getElementById("highlightDuplicatesToggle");

    const REQUESTED_LABELS_STORAGE_KEY = 'requestedCdekLabels';

    function getRequestedLabels() {
        const stored = localStorage.getItem(REQUESTED_LABELS_STORAGE_KEY);
        return stored ? JSON.parse(stored) : [];
    }

    function storeMultipleRequestedLabels(trackNumbers) {
        if (!trackNumbers || trackNumbers.length === 0) return;
        const current = getRequestedLabels();
        let updated = false;
        trackNumbers.forEach(trackNumber => {
            if (trackNumber && !current.includes(trackNumber)) {
                current.push(trackNumber);
                updated = true;
            }
        });
        if (updated) {
            localStorage.setItem(REQUESTED_LABELS_STORAGE_KEY, JSON.stringify(current));
        }
    }

    function clearAllRequestedLabels() {
        localStorage.removeItem(REQUESTED_LABELS_STORAGE_KEY);
        applyRowHighlighting();
        displayUserMessage("Отметки о запрошенных этикетках сброшены.", "success");
    }

    function applyRowHighlighting() {
        const requested = getRequestedLabels();
        document.querySelectorAll("table tbody tr").forEach(row => {
            const trackNumber = row.dataset.ozonTrackNumber;
            if (trackNumber && requested.includes(trackNumber)) {
                row.classList.add('label-requested');
            } else {
                row.classList.remove('label-requested');
            }
        });
    }
    
    applyRowHighlighting();

    function displayUserMessage(message, type = "error") {
        USER_MESSAGES_DIV.innerHTML = ''; 
        const messageDiv = document.createElement('div');
        messageDiv.className = `alert alert-${type === "error" ? "danger" : "success"} alert-dismissible fade show`;
        messageDiv.setAttribute('role', 'alert');
        messageDiv.innerHTML = `${message} <button type="button" class="close" data-dismiss="alert" aria-label="Close"><span aria-hidden="true">&times;</span></button>`;
        USER_MESSAGES_DIV.appendChild(messageDiv);
        
        setTimeout(() => {
            if (messageDiv.parentElement) {
                $(messageDiv).alert('close'); 
            }
        }, 7000);
    }

    function updateGetLabelsButtonState() {
        const anySelected = Array.from(document.querySelectorAll('.row-selector:checked'))
                               .filter(cb => cb.closest('tr').style.display !== 'none') 
                               .length > 0;
        if (getCdekLabelsBtn) getCdekLabelsBtn.disabled = !anySelected;
        updateSelectedOrdersDisplay();
    }

    function updateSelectAllCheckboxState() {
        if (!selectAllCheckbox) return;
        const visibleRows = Array.from(tbody.querySelectorAll('tr')).filter(r => r.style.display !== 'none');
        const visibleSelectedRows = visibleRows.filter(r => r.querySelector('.row-selector').checked).length;
        
        if (visibleRows.length === 0) {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = false;
        } else if (visibleSelectedRows === 0) {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = false;
        } else if (visibleSelectedRows === visibleRows.length) {
            selectAllCheckbox.checked = true;
            selectAllCheckbox.indeterminate = false;
        } else {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = true;
        }
        updateSelectedOrdersDisplay();
    }

    function updateSelectedOrdersDisplay() {
        if (!SELECTED_ORDERS_INFO_SPAN) return;

        const visibleRows = Array.from(tbody.querySelectorAll('tr')).filter(r => r.style.display !== 'none');
        const selectedVisibleRowsCount = visibleRows.filter(r => r.querySelector('.row-selector').checked).length;
        const totalVisibleRowsCount = visibleRows.length;

        if (totalVisibleRowsCount > 0) {
            SELECTED_ORDERS_INFO_SPAN.textContent = `Выбрано: ${selectedVisibleRowsCount} из ${totalVisibleRowsCount}`;
        } else {
            SELECTED_ORDERS_INFO_SPAN.textContent = "";
        }
    }

    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', function () {
            const isChecked = this.checked;
            Array.from(tbody.querySelectorAll('tr')).forEach(row => {
                if (row.style.display !== 'none') { 
                    row.querySelector('.row-selector').checked = isChecked;
                }
            });
            updateGetLabelsButtonState();
        });
    }

    rowSelectorCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function () {
            updateGetLabelsButtonState();
            updateSelectAllCheckboxState(); 
        });
    });

    filterInputs.forEach(input => {
        input.addEventListener('keyup', function () {
            const columnName = this.dataset.columnName;
            const filterValue = this.value.toLowerCase();
            let rowsToProcess = tbody.querySelectorAll('tr');

            rowsToProcess.forEach(row => {
                let cellText = '';
                const headerCells = Array.from(table.querySelectorAll('thead th'));
                let columnIndex = -1;
                for(let i=0; i < headerCells.length; i++){
                    if(headerCells[i].textContent.trim() === columnName){
                        columnIndex = i;
                        break;
                    }
                }
                
                if(columnIndex !== -1 && row.cells.length > columnIndex){
                    const dataCellIndex = columnIndex; 
                    if (row.cells[dataCellIndex]) { 
                         cellText = row.cells[dataCellIndex].textContent.toLowerCase() || row.cells[dataCellIndex].innerText.toLowerCase();
                    }
                }

                if (cellText.includes(filterValue)) {
                    row.style.display = "";
                } else {
                    row.style.display = "none";
                }
            });
        });
    });

    // Функция для ФИЛЬТРАЦИИ по дубликатам трек-номеров
    function filterByDuplicateTracks() { 
        if (!HIGHLIGHT_DUPLICATES_TOGGLE || !tbody) return;

        const potentiallyVisibleRows = Array.from(tbody.querySelectorAll('tr')).filter(r => r.style.display !== 'none');

        if (HIGHLIGHT_DUPLICATES_TOGGLE.checked) {
            const trackNumberCounts = {};
            potentiallyVisibleRows.forEach(row => {
                const trackNumber = row.dataset.ozonTrackNumber;
                if (trackNumber) {
                    trackNumberCounts[trackNumber] = (trackNumberCounts[trackNumber] || 0) + 1;
                }
            });
            const duplicateTrackNumbers = Object.keys(trackNumberCounts).filter(tn => trackNumberCounts[tn] > 1);
            potentiallyVisibleRows.forEach(row => {
                const trackNumber = row.dataset.ozonTrackNumber;
                if (!trackNumber || !duplicateTrackNumbers.includes(trackNumber)) {
                    row.style.display = 'none'; 
                }
            });
        }
        // Если чекбокс не нажат, masterFilter уже обеспечил правильную видимость.
    }

    function masterFilter() {
        const requestedLabels = getRequestedLabels();
        const downloadFilterValue = DOWNLOAD_STATUS_FILTER_SELECT.value;
        const columnFilters = Array.from(filterInputs).map(input => ({
            value: input.value.toLowerCase(),
            columnName: input.dataset.columnName
        }));

        const allDomRows = Array.from(tbody.querySelectorAll('tr')); 

        allDomRows.forEach(row => {
            let displayRow = true;

            const trackNumber = row.dataset.ozonTrackNumber;
            const isDownloaded = trackNumber && requestedLabels.includes(trackNumber);
            if (downloadFilterValue === "downloaded" && !isDownloaded) {
                displayRow = false;
            }
            if (downloadFilterValue === "not_downloaded" && isDownloaded) {
                displayRow = false;
            }

            if (displayRow) {
                const headerCells = Array.from(table.querySelectorAll('thead tr:first-child th'));
                for (const filter of columnFilters) {
                    if (filter.value) { 
                        let columnIndex = -1;
                        for (let i = 0; i < headerCells.length; i++) {
                            if (headerCells[i].textContent.trim() === filter.columnName) {
                                columnIndex = i;
                                break;
                            }
                        }
                        if (columnIndex !== -1) {
                            const cell = row.cells[columnIndex];
                            if (cell) {
                                const cellText = (cell.textContent || cell.innerText).toLowerCase();
                                if (!cellText.includes(filter.value)) {
                                    displayRow = false;
                                    break; 
                                }
                            }
                        }
                    }
                }
            }
            row.style.display = displayRow ? "" : "none";
        });
        
        filterByDuplicateTracks(); // Применяем фильтр дубликатов ПОСЛЕ основных

        // Обновляем состояния кнопок и счетчиков в самом конце, ПОСЛЕ ВСЕХ фильтраций
        updateSelectedOrdersDisplay(); 
        updateSelectAllCheckboxState(); 
        updateGetLabelsButtonState(); // Добавлено обновление состояния кнопки здесь
    }
    
    applyRowHighlighting();
    masterFilter(); 

    if (getCdekLabelsBtn) {
        getCdekLabelsBtn.addEventListener('click', function () {
            const selectedRows = Array.from(document.querySelectorAll('.row-selector:checked'))
                                     .map(cb => cb.closest('tr'));
            const trackNumbers = selectedRows.map(row => row.dataset.ozonTrackNumber).filter(tn => tn);

            if (trackNumbers.length === 0) {
                displayUserMessage("Пожалуйста, выберите заказы для получения этикеток.", "error");
                return;
            }

            if (LOADER_DIV) LOADER_DIV.style.display = 'block';
            USER_MESSAGES_DIV.innerHTML = '';

            fetch(getCdekLabelsUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ ozon_tracking_numbers: trackNumbers })
            })
            .then(response => {
                if (LOADER_DIV) LOADER_DIV.style.display = 'none';
                const contentType = response.headers.get("content-type");
                if (response.ok && contentType) {
                    if (contentType.includes("application/pdf")) {
                        storeMultipleRequestedLabels(trackNumbers);
                        applyRowHighlighting(); 
                        displayUserMessage("Запрос на этикетки успешно обработан. Начинается загрузка PDF.", "success");
                        response.blob().then(blob => {
                            const url = window.URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = `cdek_labels_${trackNumbers.length}_orders.pdf`;
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            window.URL.revokeObjectURL(url);
                        });
                    } else if (contentType.includes("application/zip")) {
                        storeMultipleRequestedLabels(trackNumbers);
                        applyRowHighlighting();
                        displayUserMessage("Запрос на этикетки успешно обработан. Начинается загрузка ZIP архива.", "success");
                        response.blob().then(blob => {
                            const url = window.URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = `cdek_labels_batch_${Date.now()}.zip`;
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            window.URL.revokeObjectURL(url);
                        });
                    } else if (contentType.includes("application/json")) {
                        response.json().then(data => {
                            if(data.error){
                                displayUserMessage(`Ошибка: ${data.error}`, "error");
                            } else if (data.message) {
                                displayUserMessage(data.message, "success"); 
                                storeMultipleRequestedLabels(trackNumbers); 
                                applyRowHighlighting();
                            } else {
                                displayUserMessage("Неожиданный JSON ответ от сервера.", "error");
                            }
                        });
                    } else {
                        displayUserMessage("Неподдерживаемый тип ответа от сервера: " + contentType, "error");
                    }
                } else {
                    response.json().then(data => {
                        displayUserMessage(`Ошибка (${response.status}): ${data.error || 'Не удалось получить этикетки.'}`, "error");
                    }).catch(() => {
                         response.text().then(text => {
                            displayUserMessage(`Ошибка (${response.status}): ${text || 'Не удалось получить этикетки.'}`, "error");
                        });
                    });
                }
            })
            .catch(error => {
                if (LOADER_DIV) LOADER_DIV.style.display = 'none';
                displayUserMessage(`Сетевая ошибка или ошибка обработки запроса: ${error}`, "error");
            });
        });
    }

    if (CLEAR_REQUESTED_LABELS_BTN) {
        CLEAR_REQUESTED_LABELS_BTN.addEventListener('click', () => {
            clearAllRequestedLabels();
            masterFilter(); 
        });
    }

    filterInputs.forEach(input => {
        input.addEventListener('keyup', masterFilter);
    });

    if (DOWNLOAD_STATUS_FILTER_SELECT) {
        DOWNLOAD_STATUS_FILTER_SELECT.addEventListener('change', masterFilter);
    }

    if (HIGHLIGHT_DUPLICATES_TOGGLE) {
        HIGHLIGHT_DUPLICATES_TOGGLE.addEventListener('change', masterFilter);
    }

    updateGetLabelsButtonState();
    updateSelectAllCheckboxState();
    updateSelectedOrdersDisplay();
}); 