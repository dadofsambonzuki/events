# page-events-display
<template id="page-events-display">
  <div v-if="event" class="row q-col-gutter-md justify-center">
    <div class="col-12 col-md-7 col-lg-6 q-gutter-y-md">
      <q-card>
        <q-img
          v-if="event.banner"
          :src="event.banner"
          transition="slide-up"
        ></q-img>
        <q-card-section class="q-pa-none">
          <h3 class="q-my-none q-pa-lg" v-text="event.name"></h3>
          <div v-html="formatDescription" class="q-pa-lg"></div>
        </q-card-section>
      </q-card>

      <div v-if="ticketTypes.length === 0 && !eventErrorLabel" class="q-pa-lg">
        <q-banner class="bg-grey-3 text-grey-8">
          <template v-slot:avatar>
            <q-icon name="info" color="grey-8" />
          </template>
          <span v-text="$t('events.no_ticket_types_available')"></span>
        </q-banner>
      </div>

      <div v-for="tt in ticketTypes" :key="tt.id" class="q-mb-md">
        <q-card>
          <q-card-section>
            <div class="row items-center q-mb-sm">
              <h5 class="q-my-none" v-text="tt.name"></h5>
            </div>
            <div v-if="tt.description" class="text-body2 q-mb-sm" v-text="tt.description"></div>
            <div class="row q-col-gutter-sm q-mb-sm">
              <div class="col-auto">
                <q-chip color="primary" text-color="white">
                  {{ tt.price }} {{ basketCurrency }}
                </q-chip>
              </div>
              <div v-if="tt.max_tickets > 0" class="col-auto">
                <q-chip outline>
                  <template v-if="tt.max_tickets - tt.sold > 0">
                    {{ tt.max_tickets - tt.sold }} remaining
                  </template>
                  <template v-else>Sold out</template>
                </q-chip>
              </div>
              <div class="col-auto">
                <q-chip outline>
                  {{ tt.available_from }} - {{ tt.available_to }}
                </q-chip>
              </div>
            </div>
            <div class="row items-center q-col-gutter-sm">
              <div class="col-12 col-md-4">
                <q-input
                  filled
                  dense
                  v-model.number="itemQuantities[tt.id]"
                  type="number"
                  :label="$t('events.quantity')"
                  min="0"
                  :max="tt.max_tickets > 0 ? tt.max_tickets - tt.sold : undefined"
                ></q-input>
              </div>
              <div class="col-auto" v-if="itemQuantities[tt.id] > 0">
                <q-btn
                  unelevated
                  color="primary"
                  :label="$t('events.add_to_basket')"
                  @click="addToBasketBtn(tt)"
                ></q-btn>
              </div>
            </div>

            <q-separator class="q-my-md" v-if="itemQuantities[tt.id] > 0 && attendeeFields[tt.id]?.length > 0" />

            <div v-if="itemQuantities[tt.id] > 0 && attendeeFields[tt.id]?.length > 0">
              <div class="row items-center q-mb-sm">
                <div class="text-subtitle2 q-mr-sm">Attendees for {{ tt.name }}</div>
                <q-checkbox
                  v-model="copyDetailsToAll[tt.id]"
                  :label="$t('events.copy_details_to_all')"
                  dense
                  @update:model-value="onCopyDetails(tt.id)"
                ></q-checkbox>
              </div>
              <div
                v-for="(attendee, idx) in attendeeFields[tt.id]"
                :key="idx"
                class="row q-col-gutter-sm q-mb-sm"
              >
                <div class="col-12 col-md-5">
                  <q-input
                    filled
                    dense
                    v-model.trim="attendee.name"
                    :label="`${$t('events.attendee')} ${idx + 1} ${$t('events.name')}`"
                    :rules="[val => nameValidation(val)]"
                  ></q-input>
                </div>
                <div class="col-12 col-md-5">
                  <q-input
                    filled
                    dense
                    v-model.trim="attendee.email"
                    type="email"
                    :label="`${$t('events.attendee')} ${idx + 1} ${$t('email')}`"
                    :rules="[val => !!val || $t('events.required'), val => emailValidation(val)]"
                    lazy-rules
                  ></q-input>
                </div>
              </div>
            </div>
          </q-card-section>
        </q-card>
      </div>

      <q-input
        v-if="event.extra?.conditional"
        filled
        dense
        class="q-mb-md"
        v-model.trim="refundAddress"
        :label="$t('events.refund_label')"
        :hint="$t('events.refund_hint', {min_tickets: event.extra?.min_tickets})"
      ></q-input>

      <div
        v-if="allowNostrNotifications"
        class="q-mb-md"
      >
        <q-input
          filled
          dense
          v-model.trim="nostrIdentifier"
          :label="$t('events.nostr_nip05_label')"
          :hint="$t('events.nostr_nip05_hint')"
        ></q-input>
      </div>

      <q-card v-if="basket.length > 0 || basketTotal > 0">
        <q-card-section>
          <h5 class="q-mt-none" v-text="$t('events.basket')"></h5>
          <div v-for="bi in basket" :key="bi.ticketTypeId" class="row items-center q-mb-sm q-col-gutter-sm">
            <div class="col" v-text="`${bi.name} x${bi.quantity}`"></div>
            <div class="col-auto" v-text="`${bi.subtotal} ${basketCurrency}`"></div>
            <div class="col-auto">
              <q-btn
                flat
                dense
                round
                icon="delete"
                color="negative"
                @click="removeFromBasket(bi.ticketTypeId)"
              ></q-btn>
            </div>
          </div>
          <q-separator></q-separator>
          <div class="q-mt-sm">
            <div class="row q-col-gutter-sm">
              <div class="col">
                <q-input
                  filled
                  dense
                  v-model.trim="promoCodeInput"
                  :label="$t('events.promo_code_comma_separated')"
                  :hint="$t('events.promo_code_hint')"
                  @keyup.enter="applyPromoCode"
                ></q-input>
              </div>
              <div class="col-auto flex items-end q-pb-sm">
                <q-btn
                  unelevated
                  color="secondary"
                  :label="$t('events.apply')"
                  :loading="applyingPromo"
                  @click="applyPromoCode"
                ></q-btn>
              </div>
            </div>
          </div>
          <div v-if="discountBreakdown.length > 0" class="q-mt-sm">
            <div v-for="(d, idx) in discountBreakdown" :key="idx" class="text-caption text-positive">
              {{ d.label }}
            </div>
          </div>
          <div class="text-h6 q-mt-md text-right">
            Total: {{ basketTotal }} {{ basketCurrency }}
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn
            unelevated
            color="primary"
            size="lg"
            :disable="!canCheckout"
            :loading="checkoutLoading"
            @click="checkout"
            v-text="$t('events.checkout')"
          ></q-btn>
        </q-card-actions>
      </q-card>

      <q-card v-show="ticketLink.show" class="q-pa-lg">
        <div class="text-center q-mb-lg">
          <q-btn
            unelevated
            size="xl"
            :href="ticketLink.data.link"
            target="_blank"
            color="primary"
            type="a"
            v-text="$t('events.link_to_ticket')"
          ></q-btn>
        </div>
      </q-card>
    </div>
  </div>
  <div v-else class="row q-col-gutter-md justify-center">
    <div class="col-12 col-md-7 col-lg-6 q-gutter-y-md">
      <q-card class="q-pa-lg">
        <q-card-section class="q-pa-none">
          <h3 class="q-my-none q-pa-lg" v-text="eventErrorLabel"></h3>
        </q-card-section>
      </q-card>
    </div>
  </div>
</template>
