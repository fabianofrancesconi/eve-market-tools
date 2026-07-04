import { create } from 'zustand'

interface UiState {
  lpSearch: string
  lpCorp: string
  lpBudget: number
  lpSalesTax: number
  lpBrokerFee: number
  lpMaxSpread: number
  lpStationId: number
  lpHideIlliquid: boolean
  lpHideUnaffordable: boolean
  lpTradeWeight: number
  lpSortKey: string
  lpSortDir: 'asc' | 'desc'
  arbRegion: number
  arbMinIsk: number
  indGroupIds: string[]
  setLpSearch: (v: string) => void
  setLpCorp: (v: string) => void
  setLpBudget: (v: number) => void
  setLpSalesTax: (v: number) => void
  setLpBrokerFee: (v: number) => void
  setLpMaxSpread: (v: number) => void
  setLpStationId: (v: number) => void
  setLpHideIlliquid: (v: boolean) => void
  setLpHideUnaffordable: (v: boolean) => void
  setLpTradeWeight: (v: number) => void
  setLpSortKey: (v: string) => void
  setLpSortDir: (v: 'asc' | 'desc') => void
  setArbRegion: (v: number) => void
  setArbMinIsk: (v: number) => void
  setIndGroupIds: (v: string[]) => void
}

export const useUiStore = create<UiState>((set) => ({
  lpSearch: '',
  lpCorp: '',
  lpBudget: 100000,
  lpSalesTax: 7.5,
  lpBrokerFee: 3.0,
  lpMaxSpread: 100,
  lpStationId: 60003760,
  lpHideIlliquid: false,
  lpHideUnaffordable: false,
  lpTradeWeight: 0.5,
  lpSortKey: 'isk_per_lp_patient',
  lpSortDir: 'desc',
  arbRegion: 10000002,
  arbMinIsk: 1000000,
  indGroupIds: [],
  setLpSearch: (v) => set({ lpSearch: v }),
  setLpCorp: (v) => set({ lpCorp: v }),
  setLpBudget: (v) => set({ lpBudget: v }),
  setLpSalesTax: (v) => set({ lpSalesTax: v }),
  setLpBrokerFee: (v) => set({ lpBrokerFee: v }),
  setLpMaxSpread: (v) => set({ lpMaxSpread: v }),
  setLpStationId: (v) => set({ lpStationId: v }),
  setLpHideIlliquid: (v) => set({ lpHideIlliquid: v }),
  setLpHideUnaffordable: (v) => set({ lpHideUnaffordable: v }),
  setLpTradeWeight: (v) => set({ lpTradeWeight: v }),
  setLpSortKey: (v) => set({ lpSortKey: v }),
  setLpSortDir: (v) => set({ lpSortDir: v }),
  setArbRegion: (v) => set({ arbRegion: v }),
  setArbMinIsk: (v) => set({ arbMinIsk: v }),
  setIndGroupIds: (v) => set({ indGroupIds: v }),
}))
