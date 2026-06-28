import {
    createServiceCard,
    updateCardStatusClasses,
    renderCardSkeleton,
    updateCardDynamicContent,
    toggleFormElements
} from './render/card.js';
import { updateModelSelect, filterVisionModels, renderModelList } from './render/models.js';

/**
 * Ana render fonksiyonu. Servis listesini döner ve kartları günceller/oluşturur.
 */
export function renderServices(services, previousServiceStates, allServiceModels, handlers, viewOptions = {}) {
    const grid = document.getElementById('services-grid');
    if (!grid) return;

    const allowedIds = new Set();
    const step = viewOptions.step || 1;
    grid.classList.toggle('grid-centered', step === 3);

    services.forEach(service => {
        if (service.status === 'disabled') return;
        const viewMode = getViewMode(service, step);
        if (viewMode === 'hidden') return;
        allowedIds.add(service.id);

        let card = document.getElementById(`service-card-${service.id}`);
        if (!card) {
            card = createServiceCard(service, grid);
        }

        const isDisabled = service.status === 'disabled';
        updateCardStatusClasses(card, isDisabled);

        if (card.dataset.viewMode !== viewMode) {
            card.innerHTML = '';
            card.dataset.viewMode = viewMode;
        }

        if (card.innerHTML === '') {
            renderCardSkeleton(card, service, isDisabled, handlers, viewMode);
        }

        updateCardDynamicContent(card, service, isDisabled, handlers, viewMode);
        toggleFormElements(card, isDisabled);
    });

    grid.querySelectorAll('.service-card').forEach(card => {
        const serviceId = card.id.replace('service-card-', '');
        if (!allowedIds.has(serviceId)) {
            card.remove();
        }
    });
}

/**
 * Servis kartı iskeletini oluşturur (HTML yapısı).
 */
export { updateModelSelect, filterVisionModels, renderModelList };

function isCoreService(service) {
    return service?.id === 'orion-hub' || service?.category === 'core' || service?.category === 'hub' || service?.category === 'router';
}

function getViewMode(service, step) {
    if (step === 1) {
        return 'hidden'; // Kurulum Ortamı step
    }
    if (step === 2) {
        return isCoreService(service) ? 'hidden' : 'models-only';
    }
    if (step === 3) {
        return isCoreService(service) ? 'hidden' : 'install';
    }
    if (step === 4) {
        return isCoreService(service) ? 'install' : 'hidden';
    }
    return 'install';
}
