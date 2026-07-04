import { create } from 'zustand'

interface UiState {
  lpSearch: string
  lpCorp: string
  lpBudget: number
  arbRegion: number
  arbMinIsk: number
  indGroupIds: string[]
  setLpSearch: (v: string) => void
  setLpCorp: (v: string) => void
  setLpBudget: (v: number) => void
  setArbRegion: (v: number) => void
  setArbMinIsk: (v: number) => void
  setIndGroupIds: (v: string[]) => void
}

export const useUiStore = create<UiState>((set) => ({
  lpSearch: '',
  lpCorp: '',
  lpBudget: 100000,
  arbRegion: 10000002,
  arbMinIsk: 1000000,
  indGroupIds: [],
  setLpSearch: (v) => set({ lpSearch: v }),
  setLpCorp: (v) => set({ lpCorp: v }),
  setLpBudget: (v) => set({ lpBudget: v }),
  setArbRegion: (v) => set({ arbRegion: v }),
  setArbMinIsk: (v) => set({ arbMinIsk: v }),
  setIndGroupIds: (v) => set({ indGroupIds: v }),
}))
